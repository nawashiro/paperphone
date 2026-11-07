"""位置情報要求の許可・拒否・サービス停止ポリシー。"""

import unittest
from unittest.mock import Mock

from paperphone.location import (
    LocationDenied,
    LocationFix,
    LocationPortal,
    LocationUnavailable,
    _DBusLocationTransport,
)


class FakePortalTransport:
    def __init__(self, *, response=0, fix=None, failure=None):
        self.response = response
        self.fix = fix or LocationFix(35.0, 139.0, 500.0)
        self.failure = failure
        self.calls = []

    def create_session(self, accuracy):
        self.calls.append(("create_session", accuracy))
        if self.failure == "create":
            raise RuntimeError("portal unavailable")
        return "/fake/session"

    def start_session(self, session):
        self.calls.append(("start_session", session))
        if self.failure == "start":
            raise RuntimeError("portal stopped")
        return "/fake/request"

    def wait_for_response(self, request, timeout_seconds):
        self.calls.append(("wait_for_response", request))
        return self.response

    def wait_for_location(self, session, timeout_seconds):
        self.calls.append(("wait_for_location", session))
        return self.fix

    def close_session(self, session):
        self.calls.append(("close_session", session))


class LocationSessionTokenTest(unittest.TestCase):
    def test_create_session_supplies_unique_valid_tokens(self):
        transport = _DBusLocationTransport.__new__(_DBusLocationTransport)
        transport._connection = Mock()
        transport._subscriptions = []
        transport._call = Mock(return_value=("/fake/session",))
        tokens = []
        for _ in range(2):
            self.assertEqual(transport.create_session(2), "/fake/session")
            method, parameters, _reply_type = transport._call.call_args.args
            self.assertEqual(method, "CreateSession")
            options = parameters.unpack()[0]
            token = options["session_handle_token"]
            self.assertRegex(token, r"^paperphone_[0-9a-f]{32}$")
            self.assertEqual(options["accuracy"], 2)
            tokens.append(token)
        self.assertNotEqual(tokens[0], tokens[1])

    def test_start_subscribes_before_call_and_accepts_legacy_path(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                transport = _DBusLocationTransport.__new__(_DBusLocationTransport)
                transport._connection = Mock()
                transport._connection.get_unique_name.return_value = ":1.42"
                transport._connection.signal_subscribe.side_effect = [10, 11]
                transport._subscriptions = []
                transport._parent_window = ""

                def call(method, parameters, reply_type):
                    self.assertEqual(method, "Start")
                    self.assertEqual(transport._connection.signal_subscribe.call_count, 1)
                    token = parameters.unpack()[2]["handle_token"]
                    path = f"/org/freedesktop/portal/desktop/request/1_42/{token}"
                    self.assertEqual(transport._connection.signal_subscribe.call_args.args[3], path)
                    return ("/legacy/request" if legacy else path,)

                transport._call = call
                path = transport.start_session("/fake/session")
                if legacy:
                    self.assertEqual(path, "/legacy/request")
                    transport._connection.signal_unsubscribe.assert_called_once_with(10)
                    self.assertEqual(transport._subscriptions, [11])
                else:
                    self.assertEqual(transport._subscriptions, [10])


class LocationPortalPolicyTest(unittest.TestCase):
    def test_allowed_request_returns_fix_and_closes_session(self):
        transport = FakePortalTransport()
        fix = LocationPortal(transport=transport).request_once()

        self.assertEqual(fix, LocationFix(35.0, 139.0, 500.0))
        self.assertEqual(transport.calls[0], ("create_session", 2))
        self.assertEqual(transport.calls[-1], ("close_session", "/fake/session"))
        self.assertEqual(sum(call[0] == "start_session" for call in transport.calls), 1)

    def test_denial_is_not_retried(self):
        transport = FakePortalTransport(response=1)
        portal = LocationPortal(transport=transport)

        with self.assertRaises(LocationDenied):
            portal.request_once()
        calls_after_denial = list(transport.calls)
        with self.assertRaises(LocationDenied):
            portal.request_once()

        self.assertEqual(transport.calls, calls_after_denial)
        self.assertEqual(sum(call[0] == "start_session" for call in transport.calls), 1)
        self.assertNotIn("wait_for_location", [call[0] for call in transport.calls])

    def test_response_two_is_service_failure_not_user_denial(self):
        transport = FakePortalTransport(response=2)
        portal = LocationPortal(transport=transport)
        with self.assertRaises(LocationUnavailable) as caught:
            portal.request_once()
        self.assertEqual(caught.exception.response_code, 2)
        self.assertFalse(portal._denied)
        self.assertEqual(transport.calls[-1], ("close_session", "/fake/session"))
        self.assertNotIn("wait_for_location", [call[0] for call in transport.calls])
        count = len(transport.calls)
        with self.assertRaises(LocationUnavailable):
            portal.request_once()
        self.assertEqual(len(transport.calls), count)

    def test_service_unavailable_is_not_retried(self):
        transport = FakePortalTransport(failure="create")
        portal = LocationPortal(transport=transport)

        with self.assertRaises(LocationUnavailable):
            portal.request_once()
        calls_after_failure = list(transport.calls)
        with self.assertRaises(LocationUnavailable):
            portal.request_once()

        self.assertEqual(transport.calls, calls_after_failure)
        self.assertEqual(sum(call[0] == "create_session" for call in transport.calls), 1)

    def test_service_stop_after_session_closes_session_without_retry(self):
        transport = FakePortalTransport(failure="start")
        portal = LocationPortal(transport=transport)

        with self.assertRaises(LocationUnavailable):
            portal.request_once()

        self.assertEqual(sum(call[0] == "start_session" for call in transport.calls), 1)
        self.assertEqual(transport.calls[-1], ("close_session", "/fake/session"))


if __name__ == "__main__":
    unittest.main()

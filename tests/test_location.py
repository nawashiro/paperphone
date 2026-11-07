"""位置情報要求の許可・拒否・サービス停止ポリシー。"""

import unittest

from paperphone.location import (
    LocationDenied,
    LocationFix,
    LocationPortal,
    LocationUnavailable,
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

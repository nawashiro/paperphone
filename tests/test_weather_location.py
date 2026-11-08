"""合成座標のみで現在地→天気と部分失敗を検証する。実通信なし。"""

import unittest
from unittest.mock import Mock

from paperphone.location import LocationDenied, LocationFix, LocationTimeout, LocationUnavailable
from paperphone.weather_location import WeatherLocationController, WeatherLocationState as S


class WeatherLocationTests(unittest.TestCase):
    def make(self, fix=LocationFix(35, 139, 500), failure=None):
        portal = Mock()
        portal.request_once.side_effect = failure
        portal.request_once.return_value = fix
        factory = Mock(return_value=portal)
        client = Mock()
        self.notifications = []
        self.jobs = []
        controller = WeatherLocationController(client, portal_factory=factory,
            submit=self.jobs.append, dispatch=lambda fn: fn(),
            on_change=lambda result: self.notifications.append(result.state))
        return controller, client, factory

    def test_permission_before_fetch_and_worker_boundary(self):
        controller, client, factory = self.make()
        controller.start("wayland:synthetic")
        factory.assert_called_once_with(parent_window="wayland:synthetic")
        self.assertEqual(self.notifications, [S.LOCATING, S.FETCHING])
        client.fetch.assert_not_called()
        self.jobs[0]()
        client.fetch.assert_called_once_with(35, 139)
        self.assertEqual(controller.result.state, S.READY)
        self.assertIs(controller.result.forecast, client.fetch.return_value)
        self.assertEqual(controller.result.accuracy_m, 500)

    def test_denied_timeout_and_service_failure_are_distinct_and_not_retried(self):
        for error, state in [(LocationDenied("private"), S.DENIED),
                             (LocationTimeout("private"), S.TIMEOUT),
                             (LocationUnavailable("private"), S.UNAVAILABLE)]:
            with self.subTest(state=state):
                controller, client, factory = self.make(failure=error)
                controller.start("x11:1")
                controller.start("x11:1")
                self.assertEqual(controller.result.state, state)
                factory.assert_called_once()
                client.fetch.assert_not_called()
                self.assertNotIn("private", repr(controller.result))

    def test_bad_or_unknown_accuracy_values_are_not_sent(self):
        for accuracy in (None, -1, float("nan"), float("inf"), True):
            with self.subTest(accuracy=accuracy):
                controller, client, _ = self.make(LocationFix(35, 139, accuracy))
                controller.start("x11:1")
                self.assertEqual(controller.result.state, S.INVALID)
                self.assertEqual(self.jobs, [])
                client.fetch.assert_not_called()

    def test_low_or_high_accuracy_is_sent_again_if_system_reports_it(self):
        # 利用者の決定: 精度の上限・下限を設けず、システムが返す値をそのまま採用する。
        for accuracy in (0, 10_001, 500_000):
            controller, client, _ = self.make(LocationFix(35, 139, accuracy))
            controller.start("x11:1")
            self.assertEqual(controller.result.state, S.FETCHING)

    def test_invalid_coordinate_never_sends(self):
        for fix in (LocationFix(91, 139, 10), LocationFix(35, 181, 10),
                    LocationFix(float("nan"), 139, 10)):
            controller, client, _ = self.make(fix)
            controller.start("x11:1")
            self.assertEqual(controller.result.state, S.INVALID)
            client.fetch.assert_not_called()

    def test_network_failure_has_no_private_exception_or_coordinates(self):
        controller, client, _ = self.make()
        client.fetch.side_effect = RuntimeError("secret-coordinate-url")
        controller.start("x11:1")
        self.jobs[0]()
        self.assertEqual(controller.result.state, S.FAILED)
        self.assertNotIn("secret", repr(controller.result))
        self.assertNotIn("139", repr(controller.result))

    def test_startup_does_not_request_location(self):
        _, client, factory = self.make()
        factory.assert_not_called()
        client.fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()

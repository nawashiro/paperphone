"""EDS更新表示とユーザーキャンセルのGTK操作fixture。"""

from datetime import date, timezone
import unittest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from paperphone.eds import EDSState, SourceDescriptor, SourceKind
from paperphone.refresh import RefreshReport, RefreshState, SourceRefreshResult
from paperphone.i18n import gettext_for
from paperphone.refresh_view import EDSRefreshView

_ = gettext_for()


class FakeJob:
    def __init__(self, callback):
        self._callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True
        self._callback(RefreshReport(RefreshState.CANCELLED, ()), None)


class FakeController:
    def __init__(self):
        self.callback = None
        self.job = None
        self.sources = None

    def refresh_async(self, sources, day, system_tz, callback):
        self.sources = tuple(sources)
        self.callback = callback
        self.job = FakeJob(callback)
        return self.job

    def finish(self, state):
        self.callback(RefreshReport(state, ()), None)


class RefreshViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()

    def make_view(self):
        controller = FakeController()
        view = EDSRefreshView(
            controller,
            get_sources=lambda: (),
            get_day=lambda: date(2025, 3, 10),
            get_system_timezone=lambda: timezone.utc,
        )
        return view, controller

    def test_refresh_shows_loading_and_cancel_stops_the_read_job(self):
        view, controller = self.make_view()

        view.refresh()

        self.assertTrue(view.is_loading)
        self.assertTrue(view.cancel_button.get_sensitive())
        self.assertFalse(view.refresh_button.get_sensitive())
        self.assertEqual(view.status_label.get_text(), _("Reading selected sources…"))
        view.cancel()

        self.assertTrue(controller.job.cancelled)
        self.assertFalse(view.is_loading)
        self.assertEqual(view.last_report.state, RefreshState.CANCELLED)
        self.assertEqual(view.status_label.get_text(), _("Refresh cancelled."))

    def test_completed_and_partial_reads_have_non_sensitive_status(self):
        view, controller = self.make_view()
        view.refresh()
        controller.finish(RefreshState.READY)
        self.assertEqual(view.status_label.get_text(), _("Refresh complete."))
        self.assertFalse(view.is_loading)

        view.refresh()
        controller.finish(RefreshState.PARTIAL)
        self.assertEqual(view.status_label.get_text(), _("Some sources could not be read."))
        self.assertFalse(view.is_loading)
        self.assertFalse(view.cancel_button.get_sensitive())

    def test_missing_remote_change_is_not_reported_as_success(self):
        view, controller = self.make_view()
        view.refresh()
        controller.callback(RefreshReport(RefreshState.PARTIAL, (
            SourceRefreshResult(
                SourceDescriptor("fixture", "Fixture", SourceKind.TASK_LIST, True, "caldav"),
                EDSState.REFRESH_UNCONFIRMED, (),
            ),
        )), None)
        self.assertEqual(view.status_label.get_text(), _(
            "CalDAV changes were not confirmed. Cached tasks may be shown."
        ))
        self.assertFalse(view.is_loading)

    def test_repeat_refresh_is_ignored_while_a_read_is_active(self):
        view, controller = self.make_view()
        view.refresh()
        view.refresh()

        self.assertEqual(controller.sources, ())
        self.assertTrue(view.is_loading)
        self.assertIsNotNone(controller.job)


if __name__ == "__main__":
    unittest.main()

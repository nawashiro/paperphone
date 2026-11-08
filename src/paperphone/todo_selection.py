"""TODOの3分類・カテゴリ横断選択を提供するGTK 4ビュー。"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, tzinfo

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from .i18n import gettext_for
from .selection import ItemKey, SelectableItem, SelectionModel
from .temporal import system_timezone
from .todo import TodoAssessment, TodoBucket, classify_todos
from .todo_reader import TodoRecord, format_temporal

_ = gettext_for()
def _bucket_labels():
    return {
        TodoBucket.TODAY: _("Today"),
        TodoBucket.UPCOMING: _("Upcoming"),
        TodoBucket.UNDATED: _("No due date"),
    }


class TodoSelectionView(Gtk.Box):
    """未完了TODOを3分類し、タブを切り替えても印刷選択を保持する。"""

    def __init__(self, selection: SelectionModel) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._selection = selection
        self._system_tz = system_timezone()
        self._records: dict[ItemKey, TodoRecord] = {}
        self._assessments: dict[ItemKey, TodoAssessment] = {}
        self._checkboxes: dict[ItemKey, Gtk.CheckButton] = {}
        self._overdue_badges: dict[ItemKey, Gtk.Label] = {}
        self._detail_labels: dict[ItemKey, Gtk.Label] = {}
        self._keys_by_bucket: dict[TodoBucket, list[ItemKey]] = {
            bucket: [] for bucket in TodoBucket
        }
        self._lists: dict[TodoBucket, Gtk.ListBox] = {}
        self._empty_labels: dict[TodoBucket, Gtk.Label] = {}
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self._stack)
        self.append(switcher)

        for bucket in TodoBucket:
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            task_list = Gtk.ListBox()
            task_list.set_selection_mode(Gtk.SelectionMode.NONE)
            task_list.add_css_class("boxed-list")
            # リストボックスへアクセシブルな名前を付け、空状態も読み上げる。
            task_list.update_property([Gtk.AccessibleProperty.LABEL],
                                      [_bucket_labels()[bucket]])
            empty = Gtk.Label(label=_("No tasks in this category."))
            empty.set_xalign(0.0)
            empty.add_css_class("dim-label")
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroller.set_child(task_list)
            scroller.set_vexpand(True)
            page.append(empty)
            page.append(scroller)
            self._stack.add_titled(page, bucket.value, _bucket_labels()[bucket])
            self._lists[bucket] = task_list
            self._empty_labels[bucket] = empty
        self.append(self._stack)

    def retranslate(self):
        """件名と選択を変更せず、固定見出しと日付テンプレートだけを翻訳する。"""
        for bucket, title in _bucket_labels().items():
            child = self._stack.get_child_by_name(bucket.value)
            self._stack.get_page(child).set_title(title)
            self._empty_labels[bucket].set_text(_("No tasks in this category."))
        for key, label in self._detail_labels.items():
            label.set_text(self._detail_text(self._records[key]))
        for label in self._overdue_badges.values():
            label.set_text(_("Overdue"))
        for key, checkbox in self._checkboxes.items():
            if not self._records[key].summary:
                checkbox.set_label(_("Untitled task"))

    @property
    def visible_bucket(self) -> TodoBucket:
        name = self._stack.get_visible_child_name()
        return next(bucket for bucket in TodoBucket if bucket.value == name)

    @property
    def selected_items(self) -> tuple[SelectableItem, ...]:
        items: list[SelectableItem] = []
        for key, todo in self._records.items():
            bucket = self._assessments[key].bucket
            if bucket is not None and self._selection.is_selected(key):
                items.append(todo.as_selectable_item(bucket, system_tz=self._system_tz))
        return tuple(items)

    def set_visible_bucket(self, bucket: TodoBucket) -> None:
        self._stack.set_visible_child_name(bucket.value)

    def row_count(self, bucket: TodoBucket) -> int:
        return len(self._keys_by_bucket[bucket])

    def checkbox_for(self, key: ItemKey) -> Gtk.CheckButton | None:
        return self._checkboxes.get(key)

    def is_overdue(self, key: ItemKey) -> bool:
        assessment = self._assessments.get(key)
        return bool(assessment and assessment.overdue)

    def overdue_badge_for(self, key: ItemKey) -> Gtk.Label | None:
        return self._overdue_badges.get(key)

    def set_tasks(
        self,
        todos: Iterable[TodoRecord],
        *,
        now: datetime,
        system_tz: tzinfo,
    ) -> None:
        self._system_tz = system_tz
        self._records.clear()
        self._assessments.clear()
        self._checkboxes.clear()
        self._overdue_badges.clear()
        self._detail_labels.clear()
        self._keys_by_bucket = {bucket: [] for bucket in TodoBucket}
        for task_list in self._lists.values():
            child = task_list.get_first_child()
            while child is not None:
                next_child = child.get_next_sibling()
                task_list.remove(child)
                child = next_child

        records = tuple(
            sorted(
                {todo.key: todo for todo in todos}.values(),
                key=lambda todo: (todo.summary.casefold(), todo.source_uid, todo.todo_uid),
            )
        )
        assessments = classify_todos(
            tuple(todo.as_todo_input() for todo in records),
            now=now,
            system_tz=system_tz,
        )
        for todo, assessment in zip(records, assessments):
            bucket = assessment.bucket
            if bucket is None:
                continue
            self._records[todo.key] = todo
            self._assessments[todo.key] = assessment
            self._append_row(todo, bucket, assessment)

        for bucket, task_list in self._lists.items():
            self._empty_labels[bucket].set_visible(self.row_count(bucket) == 0)

    def _append_row(
        self,
        todo: TodoRecord,
        bucket: TodoBucket,
        assessment: TodoAssessment,
    ) -> None:
        checkbox = Gtk.CheckButton(label=todo.summary or _("Untitled task"))
        checkbox.set_use_underline(False)
        checkbox.set_hexpand(True)
        label = checkbox.get_child()
        if isinstance(label, Gtk.Label):
            label.set_wrap(True)
            label.set_xalign(0.0)
        checkbox.set_active(self._selection.is_selected(todo.key))
        checkbox.connect("toggled", self._on_toggled, todo.key)

        row_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        row_content.append(checkbox)
        detail = self._detail_text(todo)
        if detail:
            detail_label = Gtk.Label(label=detail)
            detail_label.set_xalign(0.0)
            detail_label.set_wrap(True)
            detail_label.add_css_class("dim-label")
            row_content.append(detail_label)
            self._detail_labels[todo.key] = detail_label
        if assessment.overdue:
            overdue_label = Gtk.Label(label=_("Overdue"))
            overdue_label.set_xalign(0.0)
            # 選択判断に関わる状態のため、dim-labelで薄くしない。
            overdue_label.add_css_class("caption")
            row_content.append(overdue_label)
            self._overdue_badges[todo.key] = overdue_label

        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.set_activatable(False)
        row.set_child(row_content)
        self._lists[bucket].append(row)
        self._checkboxes[todo.key] = checkbox
        self._keys_by_bucket[bucket].append(todo.key)

    def _detail_text(self, todo: TodoRecord) -> str:
        parts: list[str] = []
        if todo.start is not None:
            parts.append(
                _("Starts: {when}").format(
                    when=format_temporal(todo.start, self._system_tz)
                )
            )
        if todo.due is not None:
            parts.append(
                _("Due: {when}").format(
                    when=format_temporal(todo.due, self._system_tz)
                )
            )
        return " · ".join(parts)

    def _on_toggled(self, checkbox: Gtk.CheckButton, key: ItemKey) -> None:
        self._selection.set_selected(key, checkbox.get_active())

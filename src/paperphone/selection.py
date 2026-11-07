"""印刷選択と再生成前まで不変の冊子スナップショット。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True, repr=False)
class ItemKey:
    """EDS項目の安定識別子。繰り返し予定はinstance_idで区別する。"""

    source_uid: str
    object_uid: str
    instance_id: str | None = None

    def __repr__(self) -> str:
        return "ItemKey(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class SelectableItem:
    key: ItemKey
    category: str
    fields: Mapping[str, str]
    completed: bool = False

    def __repr__(self) -> str:
        return (
            f"SelectableItem(category={self.category!r}, completed={self.completed!r}, "
            f"field_count={len(self.fields)})"
        )


class SelectionModel:
    """カテゴリ表示と独立して選択を保持し、消失項目を通知可能にする。"""

    def __init__(self) -> None:
        self._selected: dict[ItemKey, None] = {}

    @property
    def selected_keys(self) -> tuple[ItemKey, ...]:
        return tuple(self._selected)

    def is_selected(self, key: ItemKey) -> bool:
        return key in self._selected

    def set_selected(self, key: ItemKey, selected: bool) -> None:
        if selected:
            self._selected.setdefault(key, None)
        else:
            self._selected.pop(key, None)

    def selected_in_category(
        self,
        items: Sequence[SelectableItem],
        category: str,
    ) -> tuple[SelectableItem, ...]:
        """カテゴリ切替用の表示結果。選択状態自体は変更しない。"""
        return tuple(
            item
            for item in items
            if item.category == category and not item.completed and item.key in self._selected
        )

    def reconcile(self, items: Sequence[SelectableItem]) -> tuple[ItemKey, ...]:
        """完了・削除済み項目を除外し、除外された選択キーを返す。"""
        available = {item.key for item in items if not item.completed}
        removed = tuple(key for key in self._selected if key not in available)
        for key in removed:
            self._selected.pop(key, None)
        return removed


@dataclass(frozen=True, slots=True, repr=False)
class SnapshotItem:
    key: ItemKey
    category: str
    fields: tuple[tuple[str, str], ...]

    def __repr__(self) -> str:
        return f"SnapshotItem(category={self.category!r}, field_count={len(self.fields)})"


@dataclass(frozen=True, slots=True)
class GenerationSnapshot:
    """プレビュー・保存・印刷で共有する、再取得前の不変スナップショット。"""

    created_at: datetime
    timezone_id: str
    items: tuple[SnapshotItem, ...]

    @classmethod
    def capture(
        cls,
        items: Sequence[SelectableItem],
        *,
        created_at: datetime,
        timezone_id: str,
    ) -> GenerationSnapshot:
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("created_atにはタイムゾーン付きdatetimeが必要です")
        if not timezone_id:
            raise ValueError("timezone_idは空にできません")

        frozen_items = tuple(
            SnapshotItem(
                key=item.key,
                category=item.category,
                fields=tuple(sorted((str(name), str(value)) for name, value in item.fields.items())),
            )
            for item in items
            if not item.completed
        )
        return cls(
            created_at=created_at,
            timezone_id=timezone_id,
            items=frozen_items,
        )

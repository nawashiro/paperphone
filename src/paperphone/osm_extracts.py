"""movisda extractsの都道府県単位取得の基本部品。

抽出は公開データ(ODbL)だが、取得は `-latest` URLとETag・Last-Modifiedによる
条件付きGETで行い、無駄な再ダウンロードをしない。応答ヘッダーの状態だけを
呼出し側に保持させ、このモジュール自体はディスクに書き込まない。
実流下での取得・検証は tools/probe_osm.py が確認する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

MOVISDA_BASE = "https://osm.download.movisda.io"
# movisdaはISO 3166-2のJP-01(北海道)...JP-47(沖縄)区画に対応(2026-11-07実測)。
MAX_EXTRACT_BYTES = 2_000_000_000  # 一県のPBF上限。異常な応答を弾くため。

INITIAL = "initial"
CONDITIONAL = "conditional"
UNCHANGED = "unchanged"
RELOADED = "reloaded"
FAILED = "failed"


class ExtractsError(RuntimeError):
    """URL・応答本文を含まない取得失敗。理由は短い分類だけ。"""


@dataclass(frozen=True)
class ExtractState:
    """前回の取得状態。公開メタデータのみで、ディスク保存は呼出し側の判断。"""

    etag: str | None = None
    last_modified: str | None = None
    bytes_count: int | None = None
    filename: str | None = None

    def has_validation_data(self) -> bool:
        return self.etag is not None or self.last_modified is not None

    def as_dict(self) -> dict:
        return {
            "etag": self.etag,
            "last_modified": self.last_modified,
            "bytes_count": self.bytes_count,
            "filename": self.filename,
        }


def prefecture_url(prefecture: int) -> str:
    """ISO 3166-2の都道府県コード(1–47)をmovisdaの`-latest` URLにする。"""
    if isinstance(prefecture, bool) or not isinstance(prefecture, int) or not 1 <= prefecture <= 47:
        raise ValueError("都道府県コードは1から47です。")
    return f"{MOVISDA_BASE}/admin/JP-{prefecture:02d}-latest.osm.pbf"


def normalized(headers: Mapping[str, str]) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items()}


@dataclass(frozen=True)
class FetchPlan:
    action: str
    headers: dict[str, str]

    @property
    def conditional(self) -> bool:
        return self.action == CONDITIONAL


def plan_fetch(state: ExtractState, base_headers: Mapping[str, str] | None = None) -> FetchPlan:
    """前回状態から次の要求を決める。ETagを優先し、無ければLast-Modified。"""
    headers = dict(base_headers or {})
    if state.etag:
        headers["If-None-Match"] = state.etag
        return FetchPlan(CONDITIONAL, headers)
    if state.last_modified:
        headers["If-Modified-Since"] = state.last_modified
        return FetchPlan(CONDITIONAL, headers)
    return FetchPlan(INITIAL, headers)


def response_state(status: int, headers: Mapping[str, str]) -> tuple[str, ExtractState]:
    """応答から取得状態の更新と結果分類を作る。304は既存状態を維持する。"""
    meta = normalized(headers)
    if status == 304:
        return UNCHANGED, ExtractState()
    if status != 200:
        return FAILED, ExtractState()
    content_type = meta.get("content-type", "")
    if "pbf" not in content_type:
        raise ExtractsError("応答がOSM PBFではありません。")
    disposition = meta.get("content-disposition", "")
    filename = None
    for part in disposition.split(";"):
        part = part.strip()
        if part.startswith("filename=") and len(part) > 9:
            filename = part[9:].strip('"')
            break
    etag = meta.get("etag") or None
    last_modified = meta.get("last-modified") or None
    if etag is None and last_modified is None:
        raise ExtractsError("再検証用ヘッダーがありません。")
    try:
        bytes_count = int(meta.get("content-length", ""))
    except ValueError:
        bytes_count = None
    if bytes_count is not None and not 1 <= bytes_count <= MAX_EXTRACT_BYTES:
        raise ExtractsError("応答サイズが不正です。")
    return RELOADED, ExtractState(etag=etag, last_modified=last_modified,
                                  bytes_count=bytes_count, filename=filename)


class ExtractChecker:
    """状態を持たない応答判定に、前回状態との整合を加えた薄いラッパ。"""

    def __init__(self, state: ExtractState):
        self.state = state

    def plan(self, base_headers: Mapping[str, str] | None = None) -> FetchPlan:
        return plan_fetch(self.state, base_headers)

    def observe(self, status: int, headers: Mapping[str, str]) -> tuple[str, "ExtractChecker"]:
        outcome, fresh = response_state(status, headers)
        if outcome == UNCHANGED:
            # 304は前回の検証データをそのまま維持する。
            return UNCHANGED, self
        if outcome == RELOADED:
            return RELOADED, ExtractChecker(fresh)
        return FAILED, self


def state_from_meta(meta: Mapping[str, str]) -> ExtractState:
    """応答ヘッダー相当のメタデータから状態を組む。fixtureの読込にも使う。"""
    outcome, state = response_state(200, meta)
    if outcome != RELOADED:
        raise ValueError("メタデータが応答として成立しません。")
    return state

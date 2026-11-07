"""日付のみ・時刻付き・floating日時を区別するドメイン型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone, tzinfo
import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True, slots=True)
class DateOnly:
    """タイムゾーン変換の対象にしない日付。"""

    value: date

    def __post_init__(self) -> None:
        if not isinstance(self.value, date) or isinstance(self.value, datetime):
            raise TypeError("DateOnlyにはdatetimeではなくdateを指定してください")


@dataclass(frozen=True, slots=True)
class ZonedDateTime:
    """オフセットを持つ時刻付き日時。"""

    value: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.value, datetime):
            raise TypeError("ZonedDateTimeにはdatetimeを指定してください")
        if self.value.tzinfo is None or self.value.utcoffset() is None:
            raise ValueError("ZonedDateTimeにはタイムゾーン付きdatetimeが必要です")


@dataclass(frozen=True, slots=True)
class FloatingDateTime:
    """特定のタイムゾーンを持たない壁時計日時。"""

    value: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.value, datetime):
            raise TypeError("FloatingDateTimeにはdatetimeを指定してください")
        if self.value.tzinfo is not None:
            raise ValueError("FloatingDateTimeにはタイムゾーンなしdatetimeが必要です")


TemporalValue = DateOnly | ZonedDateTime | FloatingDateTime


def _zoneinfo_from_key(key: str) -> tzinfo | None:
    key = key.strip()
    if key.startswith(":"):
        key = key[1:]
    if not key:
        return None

    if key.startswith("/"):
        path = Path(key)
        for root in (Path("/usr/share/zoneinfo"), Path("/usr/lib/zoneinfo")):
            try:
                key = path.resolve().relative_to(root.resolve()).as_posix()
                break
            except (OSError, ValueError):
                continue
        else:
            return None

    try:
        return ZoneInfo(key)
    except (ValueError, ZoneInfoNotFoundError):
        return None


def system_timezone() -> tzinfo:
    """実行時のシステムタイムゾーンを取得する。結果を長期キャッシュしない。"""

    tz_environment = os.environ.get("TZ")
    if tz_environment:
        zone = _zoneinfo_from_key(tz_environment)
        if zone is not None:
            return zone

    localtime = Path("/etc/localtime")
    try:
        resolved = localtime.resolve(strict=True)
        for root in (Path("/usr/share/zoneinfo"), Path("/usr/lib/zoneinfo")):
            try:
                key = resolved.relative_to(root.resolve()).as_posix()
                zone = _zoneinfo_from_key(key)
                if zone is not None:
                    return zone
            except (OSError, ValueError):
                continue
        with localtime.open("rb") as stream:
            return ZoneInfo.from_file(stream, key="system-localtime")
    except (OSError, ValueError):
        pass

    try:
        configured_key = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        zone = _zoneinfo_from_key(configured_key)
        if zone is not None:
            return zone
    except OSError:
        pass

    return datetime.now().astimezone().tzinfo or timezone.utc


def to_system_local(
    value: TemporalValue,
    *,
    system_tz: tzinfo | None = None,
) -> date | datetime:
    """日時値をシステムタイムゾーンで解釈・表示する。

    DateOnlyは日付のまま保持する。FloatingDateTimeは壁時計の年月日時分を
    変えずにシステムタイムゾーンへ割り当て、ZonedDateTimeは同じ瞬間を
    システムタイムゾーンへ変換する。
    """

    if isinstance(value, DateOnly):
        return value.value

    zone = system_timezone() if system_tz is None else system_tz
    if isinstance(value, ZonedDateTime):
        return value.value.astimezone(zone)
    if isinstance(value, FloatingDateTime):
        return value.value.replace(tzinfo=zone)
    raise TypeError(f"未対応の日時型です: {type(value).__name__}")

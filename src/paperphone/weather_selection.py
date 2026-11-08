"""システムの今日の9・12・18時に近い異なる短期予報を選ぶ。"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Iterable

from .weather import Forecast

UTC = timezone.utc
WINDOW = timedelta(minutes=90)


@dataclass(frozen=True)
class ForecastSlot:
    target_hour: int
    forecast: Forecast | None


def select_today_forecasts(forecasts: Iterable[Forecast], now: datetime) -> tuple[ForecastSlot, ...]:
    """nowのタイムゾーンをシステムゾーンとして扱い、実時刻を変えずに選ぶ。

    欠損はNoneとし、他の日・6時間予報・重複した実時刻で補わない。
    実通信・システム時計へ直接依存させず、呼出し元から現在時刻を注入する。
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("システムタイムゾーン付きの現在時刻が必要です。")
    zone = now.tzinfo
    day = now.date()
    candidates = {}
    for forecast in forecasts:
        if (forecast.time.tzinfo is None or forecast.time.utcoffset() is None
                or forecast.period_end.tzinfo is None or forecast.period_end.utcoffset() is None):
            continue
        stamp = forecast.time.astimezone(UTC)
        if (forecast.time.astimezone(zone).date() != day
                or forecast.period_end.astimezone(UTC) - stamp != timedelta(hours=1)):
            continue
        candidates.setdefault(stamp, forecast)
    result = []
    for hour in (9, 12, 18):
        target = datetime.combine(day, time(hour), tzinfo=zone).astimezone(UTC)
        allowed = [stamp for stamp in candidates if abs(stamp - target) <= WINDOW]
        if not allowed:
            result.append(ForecastSlot(hour, None))
            continue
        best = min(allowed, key=lambda stamp: (abs(stamp - target), -stamp.timestamp()))
        result.append(ForecastSlot(hour, candidates.pop(best)))
    return tuple(result)


def remaining_targets(now: datetime) -> bool:
    """今日の9・12・18時のどれかがまだ選べる間だけTrue。

    18時枠の選択窓（前後90分）が閉じた後は、今日の天気はもう表示できない。
    実通信を行う前にこの判定を使い、無意味な取得をしない。
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("システムタイムゾーン付きの現在時刻が必要です。")
    zone = now.tzinfo
    day = now.date()
    for hour in (9, 12, 18):
        end = (datetime.combine(day, time(hour), tzinfo=zone) + WINDOW).astimezone(UTC)
        if now < end:
            return True
    return False

"""実予報時刻・期間を保持する不変の天気紙面モデル。GTK/Cairoに依存しない。"""

from dataclasses import dataclass
from datetime import datetime

from .i18n import gettext_for
from .selection import ItemKey, SelectableItem
from .weather import ATTRIBUTION_SHORT, ForecastResult
from .weather_selection import select_today_forecasts

_ = gettext_for()

# MET Norway公式weathericons/weather/legend.csv（2026-11-08参照）の識別子。
# https://github.com/metno/weathericons/blob/main/weather/legend.csv
# 見知らぬ識別子を既知の状態に読み替えない。画像資源はこの段階では利用しない。
VARIANT_SYMBOLS = frozenset("""
clearsky fair partlycloudy
lightrainshowers rainshowers heavyrainshowers
lightrainshowersandthunder rainshowersandthunder heavyrainshowersandthunder
lightsleetshowers sleetshowers heavysleetshowers
lightssleetshowersandthunder sleetshowersandthunder heavysleetshowersandthunder
lightsnowshowers snowshowers heavysnowshowers
lightssnowshowersandthunder snowshowersandthunder heavysnowshowersandthunder
""".split())
PLAIN_SYMBOLS = frozenset("""
cloudy fog lightrain rain heavyrain lightrainandthunder rainandthunder heavyrainandthunder
lightsleet sleet heavysleet lightsleetandthunder sleetandthunder heavysleetandthunder
lightsnow snow heavysnow lightsnowandthunder snowandthunder heavysnowandthunder
""".split())


def known_symbol(code: str) -> bool:
    if code in PLAIN_SYMBOLS or code in VARIANT_SYMBOLS:
        return True
    base, _, variant = code.rpartition("_")
    return base in VARIANT_SYMBOLS and variant in ("day", "night", "polartwilight")


@dataclass(frozen=True, repr=False)
class WeatherRow:
    target_hour: int
    actual_at: datetime | None
    period_end: datetime | None
    temperature_c: float | None
    symbol: str | None
    symbol_known: bool

    @property
    def available(self):
        return self.actual_at is not None


@dataclass(frozen=True, repr=False)
class WeatherSheet:
    rows: tuple[WeatherRow, ...]
    fetched_at: datetime
    checked_at: datetime
    expires_at: datetime | None
    deprecated: bool
    expired: bool
    attribution: str = ATTRIBUTION_SHORT

    def as_selectable_item(self) -> SelectableItem:
        """共通の不変生成スナップショットへ投影する。座標・元応答は含めない。"""
        fields = {
            "fetched_at": self.fetched_at.isoformat(),
            "checked_at": self.checked_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else "",
            "deprecated": str(self.deprecated).lower(),
            "expired": str(self.expired).lower(),
            "attribution": self.attribution,
            "text": "\n".join(weather_lines(self)),
        }
        for row in self.rows:
            prefix = f"forecast_{row.target_hour}_"
            fields.update({
                prefix + "actual_at": row.actual_at.isoformat() if row.actual_at else "",
                prefix + "period_end": row.period_end.isoformat() if row.period_end else "",
                prefix + "temperature_c": str(row.temperature_c) if row.available else "",
                prefix + "symbol": row.symbol or "",
                prefix + "symbol_known": str(row.symbol_known).lower(),
            })
        return SelectableItem(ItemKey("weather", "today"), "weather", fields)


def build_weather_sheet(result: ForecastResult, *, now: datetime) -> WeatherSheet:
    """nowのシステムゾーンで今日の予報を選ぶ。目安時刻を実時刻に代入しない。"""
    slots = select_today_forecasts(result.forecasts, now)
    zone = now.tzinfo
    rows = []
    for slot in slots:
        forecast = slot.forecast
        rows.append(WeatherRow(
            slot.target_hour,
            forecast.time.astimezone(zone) if forecast else None,
            forecast.period_end.astimezone(zone) if forecast else None,
            forecast.temperature_c if forecast else None,
            forecast.symbol if forecast else None,
            known_symbol(forecast.symbol) if forecast else False,
        ))
    return WeatherSheet(tuple(rows), result.fetched_at.astimezone(zone),
                        result.checked_at.astimezone(zone),
                        result.expires_at.astimezone(zone) if result.expires_at else None,
                        result.deprecated,
                        result.expires_at is not None and now >= result.expires_at)


def display_time(value: datetime) -> str:
    # 今日の紙面に合う短い表示。日付とUTCオフセットは紙面に出さない。
    return value.strftime("%H:%M")


def weather_lines(sheet: WeatherSheet) -> tuple[str, ...]:
    """画面と紙面の共通表示。気温の瞬間と天気の期間を区別する。"""
    lines = []
    for row in sheet.rows:
        if not row.available:
            # 過ぎた時刻や欠落した枠は黙って省略する。
            continue
        condition = (row.symbol if row.symbol_known else _("Unknown weather condition"))
        lines.append(_("At {when}: {temperature} °C; {period} to {end}, {condition}").format(
            when=display_time(row.actual_at), temperature=f"{row.temperature_c:g}",
            period=display_time(row.actual_at), end=display_time(row.period_end),
            condition=condition))
    lines.append(sheet.attribution)
    return tuple(lines)

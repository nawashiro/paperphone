"""MET Norway短期予報の直接取得。座標・応答・識別情報はメモリ内限定。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from email.utils import parsedate_to_datetime
import gzip
import json
import math
import ssl
from typing import Callable, Mapping
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
import zlib

UTC = timezone.utc
ENDPOINT = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
ATTRIBUTION = "MET Norway — excerpted and formatted / 抜粋・整形; CC BY 4.0 https://creativecommons.org/licenses/by/4.0/"
# 再取得の最短間隔。期限欠落・失敗時にも連続要求しない。
MIN_INTERVAL = timedelta(minutes=10)
MAX_BODY = 8 * 1024 * 1024


class WeatherError(RuntimeError):
    """詳細なURL・本文・連絡先を含まない取得失敗。"""


@dataclass(frozen=True)
class Forecast:
    time: datetime
    temperature_c: float
    symbol: str
    period_end: datetime


@dataclass(frozen=True)
class ForecastResult:
    forecasts: tuple[Forecast, ...]
    fetched_at: datetime
    checked_at: datetime
    expires_at: datetime | None
    deprecated: bool = False


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname != "api.met.no" or parsed.port not in (None, 443):
            raise WeatherError("予報サービス外への転送を拒否しました。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _http_get(url: str, headers: Mapping[str, str]) -> Response:
    # HTTPキャッシュ・Cookie・プロキシの暗黙利用なし。TLS証明書を検証する。
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()), _SafeRedirect())
    try:
        try:
            stream = opener.open(Request(url, headers=dict(headers)), timeout=30)
        except HTTPError as error:
            stream = error
        with stream:
            body = stream.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise WeatherError("予報応答が大きすぎます。")
            return Response(stream.code, dict(stream.headers.items()), body)
    except Exception:
        # urllib例外は座標入りURLを持つ可能性があるため外へ渡さない。
        raise WeatherError("予報サービスに接続できません。") from None


def _http_date(value: str | None) -> datetime | None:
    try:
        result = parsedate_to_datetime(value) if value else None
        return result.astimezone(UTC) if result and result.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def _coordinate(value: float, limit: int) -> str:
    if isinstance(value, bool) or not math.isfinite(value) or not -limit <= value <= limit:
        raise ValueError("座標が不正です。")
    return str(Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_DOWN))


def parse_forecasts(body: bytes) -> tuple[Forecast, ...]:
    """瞬間の気温と明示された1時間の天気だけを保持する。"""
    try:
        document = json.loads(body)
        if document["properties"]["meta"]["units"]["air_temperature"] != "celsius":
            raise ValueError
        series = document["properties"]["timeseries"]
        if not isinstance(series, list):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise WeatherError("予報応答の形式が不正です。") from None
    forecasts: dict[datetime, Forecast] = {}
    for item in series:
        try:
            time = datetime.fromisoformat(item["time"])
            if time.tzinfo is None:
                continue
            time = time.astimezone(UTC)
            data = item["data"]
            temperature = data["instant"]["details"]["air_temperature"]
            symbol = data["next_1_hours"]["summary"]["symbol_code"]
            if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not math.isfinite(temperature):
                continue
            if not isinstance(symbol, str) or not symbol:
                continue
            # 未知の記号はそのまま保持。UIで既知状態へ偽装しない。
            forecasts.setdefault(time, Forecast(time, float(temperature), symbol, time + timedelta(hours=1)))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return tuple(forecasts[time] for time in sorted(forecasts))


class WeatherClient:
    """同期I/Oアダプター。UIではワーカーから呼ぶ。自動更新・永続化なし。"""

    def __init__(self, application_id: str, version: str, contact: str, *,
                 transport: Callable[[str, Mapping[str, str]], Response] = _http_get,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        if any(not value or not value.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in value)
               for value in (application_id, version, contact)):
            raise ValueError("クライアント識別情報が不正です。")
        self._user_agent = f"{application_id}/{version} ({contact})"
        self._transport = transport
        self._clock = clock
        self._key: tuple[str, str] | None = None
        self._result: ForecastResult | None = None
        self._last_modified: str | None = None
        self._etag: str | None = None
        self._next_request = datetime.min.replace(tzinfo=UTC)

    def fetch(self, latitude: float, longitude: float) -> ForecastResult:
        key = (_coordinate(latitude, 90), _coordinate(longitude, 180))
        now = self._clock().astimezone(UTC)
        same = key == self._key
        if same and self._result and self._result.expires_at and now < self._result.expires_at:
            return self._result
        if now < self._next_request:
            raise WeatherError("予報の再取得待機中です。以前の予報は最新とみなせません。")
        headers = {"User-Agent": self._user_agent, "Accept": "application/json", "Accept-Encoding": "gzip, deflate"}
        if same and self._last_modified:
            headers["If-Modified-Since"] = self._last_modified
        if same and self._etag:
            headers["If-None-Match"] = self._etag
        self._next_request = now + MIN_INTERVAL
        try:
            response = self._transport(ENDPOINT + "?" + urlencode(dict(zip(("lat", "lon"), key))), headers)
            received = self._clock().astimezone(UTC)
            meta = {k.lower(): v for k, v in response.headers.items()}
            expires = _http_date(meta.get("expires"))
            if response.status == 429:
                retry = meta.get("retry-after", "")
                try:
                    until = received + timedelta(seconds=max(0, int(retry)))
                except (ValueError, OverflowError):
                    until = _http_date(retry) or received + MIN_INTERVAL
                self._next_request = max(self._next_request, until)
                raise WeatherError("予報サービスの要求上限です。再取得を待ってください。")
            if response.status == 304 and same and self._result:
                result = ForecastResult(self._result.forecasts, self._result.fetched_at, received, expires, self._result.deprecated)
            elif response.status in (200, 203):
                body = response.body
                encoding = meta.get("content-encoding", "identity").lower()
                if encoding == "gzip":
                    # 展開後も上限を適用し、圧縮爆弾を避ける。
                    import io
                    with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
                        body = stream.read(MAX_BODY + 1)
                elif encoding == "deflate":
                    body = zlib.decompressobj().decompress(body, MAX_BODY + 1)
                elif encoding != "identity":
                    raise WeatherError("予報の圧縮形式に対応していません。")
                if len(body) > MAX_BODY:
                    raise WeatherError("予報応答が大きすぎます。")
                result = ForecastResult(parse_forecasts(body), received, received, expires, response.status == 203)
            else:
                raise WeatherError("予報サービスが取得を拒否しました。")
            if not same or response.status != 304:
                self._last_modified = None
                self._etag = None
            self._last_modified = meta.get("last-modified", self._last_modified)
            self._etag = meta.get("etag", self._etag)
            self._key, self._result = key, result
            if expires:
                self._next_request = max(self._next_request, expires)
            return result
        except WeatherError:
            raise
        except Exception:
            raise WeatherError("予報を取得できません。") from None

"""XDG Locationポータルから一度だけ現在地を読む。"""

from __future__ import annotations

from dataclasses import dataclass
import math
import uuid
from typing import Protocol

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib


PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
LOCATION_INTERFACE = "org.freedesktop.portal.Location"


@dataclass(frozen=True)
class LocationFix:
    """位置情報ポータルが返した座標。永続化せず呼出元のメモリ内だけで扱う。"""

    latitude: float
    longitude: float
    accuracy_m: float | None = None


class LocationError(RuntimeError):
    """位置情報を取得できなかった。"""


class LocationDenied(LocationError):
    """利用者が位置情報要求を拒否または取消した。"""


class LocationUnavailable(LocationError):
    """ポータル、サービス、または座標fixを利用できなかった。"""


class _PortalTransport(Protocol):
    def create_session(self, accuracy: int) -> str: ...
    def start_session(self, session: str) -> str: ...
    def wait_for_response(self, request: str, timeout_seconds: int) -> int: ...
    def wait_for_location(self, session: str, timeout_seconds: int) -> LocationFix: ...
    def close_session(self, session: str) -> None: ...


class LocationPortal:
    """Location portalを通じて一回だけ読み取り、失敗時に自動再試行しない。"""

    def __init__(
        self,
        *,
        accuracy: int = 2,
        timeout_seconds: int = 45,
        parent_window: str = "",
        transport: _PortalTransport | None = None,
    ) -> None:
        if accuracy not in range(6):
            raise ValueError("accuracyは0から5で指定してください")
        if timeout_seconds < 1:
            raise ValueError("timeout_secondsは1以上で指定してください")
        self._accuracy = accuracy
        self._timeout_seconds = timeout_seconds
        self._parent_window = parent_window
        self._transport = transport
        self._denied = False
        self._attempted = False

    def request_once(self) -> LocationFix:
        """許可を要求し、座標を返す。同一インスタンスから再要求しない。"""
        if self._denied:
            raise LocationDenied("位置情報は拒否済みです。自動再要求しません。")
        if self._attempted:
            raise LocationUnavailable("この位置情報要求は既に終了しています。")
        self._attempted = True

        transport = self._transport
        session: str | None = None
        try:
            if transport is None:
                transport = _DBusLocationTransport(self._parent_window)
                self._transport = transport
            session = transport.create_session(self._accuracy)
            request = transport.start_session(session)
            response = transport.wait_for_response(request, self._timeout_seconds)
            if response != 0:
                self._denied = True
                raise LocationDenied("位置情報要求が拒否または取消されました。")
            return transport.wait_for_location(session, self._timeout_seconds)
        except LocationError:
            raise
        except Exception as error:
            raise LocationUnavailable("位置情報サービスを利用できません。") from error
        finally:
            if transport is not None and session is not None:
                try:
                    transport.close_session(session)
                except Exception:
                    pass


class _DBusLocationTransport:
    """Gio経由のLocation portal呼出し。変更系メソッドは使用しない。"""

    def __init__(self, parent_window: str) -> None:
        self._connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._parent_window = parent_window
        self._loop = GLib.MainLoop()
        self._session: str | None = None
        self._request: str | None = None
        self._response: int | None = None
        self._fix: LocationFix | None = None
        self._timed_out = False
        self._subscriptions: list[int] = []

    def create_session(self, accuracy: int) -> str:
        options = {
            "accuracy": GLib.Variant("u", accuracy),
            "distance-threshold": GLib.Variant("u", 0),
            "time-threshold": GLib.Variant("u", 0),
        }
        self._session = self._call(
            "CreateSession",
            GLib.Variant("(a{sv})", (options,)),
            GLib.VariantType.new("(o)"),
        )[0]
        self._subscriptions.append(
            self._connection.signal_subscribe(
                PORTAL_NAME,
                LOCATION_INTERFACE,
                "LocationUpdated",
                PORTAL_PATH,
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_location_updated,
                None,
            )
        )
        return self._session

    def start_session(self, session: str) -> str:
        options = {"handle_token": GLib.Variant("s", "paperphone_" + uuid.uuid4().hex)}
        self._request = self._call(
            "Start",
            GLib.Variant("(osa{sv})", (session, self._parent_window, options)),
            GLib.VariantType.new("(o)"),
        )[0]
        self._subscriptions.append(
            self._connection.signal_subscribe(
                PORTAL_NAME,
                "org.freedesktop.portal.Request",
                "Response",
                self._request,
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_response,
                None,
            )
        )
        return self._request

    def wait_for_response(self, request: str, timeout_seconds: int) -> int:
        del request  # The signal subscription is already bound to the request path.
        self._wait_until(lambda: self._response is not None, timeout_seconds)
        assert self._response is not None
        return self._response

    def wait_for_location(self, session: str, timeout_seconds: int) -> LocationFix:
        del session  # LocationUpdated is filtered by the active session in its callback.
        self._wait_until(lambda: self._fix is not None, timeout_seconds)
        assert self._fix is not None
        return self._fix

    def close_session(self, session: str) -> None:
        for subscription in self._subscriptions:
            self._connection.signal_unsubscribe(subscription)
        self._subscriptions.clear()
        self._connection.call_sync(
            PORTAL_NAME,
            session,
            "org.freedesktop.portal.Session",
            "Close",
            None,
            None,
            Gio.DBusCallFlags.NONE,
            5000,
            None,
        )

    def _call(self, method: str, parameters: GLib.Variant, reply_type: GLib.VariantType):
        return self._connection.call_sync(
            PORTAL_NAME,
            PORTAL_PATH,
            LOCATION_INTERFACE,
            method,
            parameters,
            reply_type,
            Gio.DBusCallFlags.NONE,
            15000,
            None,
        ).unpack()

    def _wait_until(self, predicate, timeout_seconds: int) -> None:
        if predicate():
            return
        self._timed_out = False

        def on_timeout() -> bool:
            self._timed_out = True
            self._loop.quit()
            return GLib.SOURCE_REMOVE

        source_id = GLib.timeout_add_seconds(timeout_seconds, on_timeout)
        self._loop.run()
        if not self._timed_out:
            GLib.source_remove(source_id)
        if self._timed_out:
            raise LocationUnavailable("位置情報ポータルの応答がタイムアウトしました。")

    def _on_response(self, _connection, _sender, _path, _interface, _signal, parameters, _data) -> None:
        response, _results = parameters.unpack()
        self._response = int(response)
        if self._response != 0 or self._fix is not None:
            self._loop.quit()

    def _on_location_updated(self, _connection, _sender, _path, _interface, _signal, parameters, _data) -> None:
        session, location = parameters.unpack()
        if session != self._session:
            return
        latitude = _number(location.get("Latitude"))
        longitude = _number(location.get("Longitude"))
        accuracy = _number(location.get("Accuracy"))
        if latitude is not None and longitude is not None and -90 <= latitude <= 90 and -180 <= longitude <= 180:
            self._fix = LocationFix(latitude, longitude, accuracy)
        location.clear()
        latitude = longitude = accuracy = None
        if self._response == 0 and self._fix is not None:
            self._loop.quit()


def _number(value: object) -> float | None:
    if hasattr(value, "unpack"):
        value = value.unpack()
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None

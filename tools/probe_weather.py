"""許可済みの実地点で一度だけ直接取得する実証。個人値は出力しない。

位置取得とMET Norwayへの送信を許可し、利用規約に同意した後だけ実行する。
連絡先は端末から非表示入力する。自動化時は --stdin-contact で標準入力から読む。
--window は実際の親ウィンドウを提示してフォーカスを待ち、ポータルへ関連付ける。
座標・応答・連絡先をファイルへ保存せず、自動再試行しない。
"""

import argparse
import getpass
import sys
from datetime import timedelta

from paperphone.location import LocationPortal
from paperphone.weather import WeatherClient, _http_get


def probe(contact, parent_window=""):
    requests = 0
    observed = {}

    def transport(url, headers):
        nonlocal requests
        requests += 1
        observed["identified"] = headers.get("User-Agent", "").endswith(f"({contact})")
        response = _http_get(url, headers)
        meta = {k.lower(): v for k, v in response.headers.items()}
        observed.update(status=response.status, expires_present="expires" in meta,
                        last_modified_present="last-modified" in meta,
                        etag_present="etag" in meta,
                        compression=meta.get("content-encoding", "identity"))
        return response

    stage = "location"
    try:
        fix = LocationPortal(parent_window=parent_window).request_once()
        stage = "forecast"
        client = WeatherClient("dev.nawashiro.PaperPhone", "0.1.0", contact, transport=transport)
        result = client.fetch(fix.latitude, fix.longitude)
        print("location_received=True")
        for name, value in observed.items():
            print(f"{name}={value}")
        print(f"short_term_count={len(result.forecasts)}")
        print(f"explicit_one_hour_periods={bool(result.forecasts) and all(f.period_end - f.time == timedelta(hours=1) for f in result.forecasts)}")
        print(f"utc_times={all(f.time.utcoffset() == timedelta() for f in result.forecasts)}")
        if result.expires_at and result.expires_at > result.checked_at:
            print(f"fresh_memory_cache_reused={client.fetch(fix.latitude, fix.longitude) is result}")
        print(f"network_requests={requests}")
        print(f"deprecated={result.deprecated}")
        return 0
    except Exception as error:
        print(f"failed_stage={stage}; error_type={type(error).__name__}; response_code={getattr(error, 'response_code', None)}; automatic_retry=False")
        return 1


def window_probe(contact, application_id):
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("GdkWayland", "4.0")
    gi.require_version("GdkX11", "4.0")
    from gi.repository import GdkWayland, GdkX11, Gio, GLib, Gtk

    GLib.set_prgname(application_id)
    application = Gtk.Application(application_id=application_id, flags=Gio.ApplicationFlags.NON_UNIQUE)
    outcome = [1]
    started = [False]

    def activate(app):
        window = Gtk.ApplicationWindow(application=app, title="Paper Phone — 天気取得の実証")
        window.set_default_size(480, 200)
        label = Gtk.Label(label="このウィンドウが手前になったら位置情報を一度取得し、\nMET Norwayへ座標を送って予報を取得します。\n座標・連絡先・予報値は記録しません。")
        window.set_child(label)
        window.present()

        def finish(parent, surface=None):
            outcome[0] = probe(contact, parent)
            if surface is not None:
                surface.unexport_handle()
            app.quit()

        def when_active():
            if started[0]:
                return GLib.SOURCE_REMOVE
            if not window.is_active():
                return GLib.SOURCE_CONTINUE
            started[0] = True
            surface = window.get_surface()
            if hasattr(surface, "export_handle"):
                def exported(toplevel, handle, _data):
                    # 親handleはポータルへの送信にのみ使い、出力しない。
                    GLib.idle_add(lambda: (finish("wayland:" + handle, toplevel), GLib.SOURCE_REMOVE)[1])
                if not surface.export_handle(exported, None):
                    print("parent_export_failed=True")
                    app.quit()
            elif hasattr(surface, "get_xid"):
                GLib.idle_add(lambda: (finish("x11:" + format(surface.get_xid(), "x")), GLib.SOURCE_REMOVE)[1])
            else:
                print("parent_export_unsupported=True")
                app.quit()
            return GLib.SOURCE_REMOVE

        def focus_timeout():
            if not started[0]:
                print("window_focus_timeout=True; no_location_request=True")
                app.quit()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(250, when_active)
        GLib.timeout_add_seconds(90, focus_timeout)

    application.connect("activate", activate)
    application.run([])
    return outcome[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stdin-contact", action="store_true")
    parser.add_argument("--window", action="store_true")
    parser.add_argument("--test-app-id", default="dev.nawashiro.PaperPhone")
    args = parser.parse_args()
    contact = sys.stdin.readline().strip() if args.stdin_contact else getpass.getpass("MET Norway連絡先（非保存）: ")
    return window_probe(contact, args.test_app_id) if args.window else probe(contact)


if __name__ == "__main__":
    sys.exit(main())

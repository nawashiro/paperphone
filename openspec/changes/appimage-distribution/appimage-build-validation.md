# AppImage同梱ビルド検証記録

## 1.1 1手順スクリプト・再現性

- 1手順: ホストから `python3 tools/build_appimage.py`。podmanでUbuntu 22.04（glibc 2.35基準）を起動し、コンテナ内でGNOMEスタック・Python同梱・AppDir組立て・appimagetoolまで一続きで実行する。中断後の再実行は `.stamp` で完了済みの段を飛ばして継続する。
- 構成の固定: 再実行では全コンポーネントの版がスクリプト内の固定値で決まるため、同じ構成のAppDir・AppImageが作られる。検証日 2026-11-09。同日、wayland・vulkan・appstream・Python同梱の修正のたびに多数回の再実行で同じ構成を確認。

| 作成物 | 大きさ |
|---|---|
| `build/appimage/paperphone-release.AppImage` | 141,239,488 バイト |

### GNOMEスタックの版

glib 2.84.4 / gobject-introspection 1.82.0 / libxmlb 0.3.21 / appstream 1.0.4 / cairo 1.18.4 / graphene 1.10.8 / pango 1.52.2 / gdk-pixbuf 2.42.12 / gtk 4.15.6 / libadwaita 1.5.8 / Python 3.10（Ubuntu 22.04のpython3.10＋venv: requirements.txt＋pycairo 1.29.2＋PyGObject 3.58.1）

### 同梱ビルドから外した箇所と理由

- GTKのwayland backendを切る（X11のみ）。Ubuntu 22.04のwayland-client 1.20・wayland-protocols 1.25がGTK 4.15の要求（>=1.21・>=1.36）に届かず、GTK同梱のwayland 1.21 subprojectはwayland-protocolsのtestsが要求するwayland-scanner dependencyを提供しない。GNOME WaylandセッションではXWaylandで動く。
- Vulkanを切る。描写はNGL（GL 3.2+）経路。Vulkanデバイス・ヘッダのAppImage同梱は扱わない。

## 1.2 Python同梱とGI接続

- Python本体・基盤stdlibをAppDirに同梱し、クリーン環境にpython3がなくても起動する。
- AppImageのPythonからの全GI読み込みをコンテナ内で確認:

```
LD_LIBRARY_PATH/GI_TYPELIB_PATH/PYTHONPATH をAppDirに向け
python3.10 -c "import gi; require Gtk 4.0, Adw 1, GdkPixbuf 2.0, Poppler 0.18, Pango 1.0, PangoCairo 1.0, PangoFc 1.0;
from gi.repository import Gtk, Adw, GdkPixbuf, Poppler, Gio, cairo, Pango, PangoCairo, PangoFc
import osmium, shapely, rapidfuzz, cairo, icalendar, dateutil.rrule"
→ ALL GI+wheel imports OK（docs/gnome-dependencies.mdの機能一覧照合済）
```

- 起動試験: 同コンテナで `xvfb-run -a ./paperphone-release.AppImage --appimage-extract-and-run`。主画面を描画してtimeout撤去の25秒まで継続（SIGTERM時まで主ループ稼働）。import失敗なし。

## 1.3 証明書・ロケール・スキーマ（フォント以外）

- 証明書: `/etc/ssl/certs` をAppDirの `etc/ssl/certs` に同梱し、AppRunのSSL_CERT_DIRで参照する。
- 日本語: jaのpaperphone.moを `lib/python3/dist-packages/paperphone/resources/locale` に同梱。
- GSettingsスキーマ: GTK・libadwaita由来のschemasと `gschemas.compiled` を `share/glib-2.0/schemas` に同梱。
- 日本語フォールバックフォントは同梱せず、ホストのfontconfigに委ねる。Ubuntu Desktopにはfonts-noto-cjkが付属する一方、フォントのフル同梱はAppImageの大幅な膨張やキャッシュ管理の二重化を招くため、最小構成を選んだ。
- GSettingsの書込競合: アプリの設定はGSettingsではなく専用ファイル（`$XDG_CONFIG_HOME/paperphone/settings.json`、PAPERPHONE_CONFIG_DIR上書き可能）に0600で書く。AppImage起動試験（1.2のxvfb実行）でGSettings由来の失敗やschema source不在の警告は現れない。glibの書込先が`XDG_CONFIG_HOME/paperphone`ファイルと矛盾する場面は存在しない。

## 既知制限

- FUSEが無い環境（コンテナ内等）では `--appimage-extract-and-run` が必要。GNOMEデスクトップでは通常の実行でmountされる。
- 配布用アプリID（`com.withgoogle.experiments.unplugged`）とアイコン（暫定折り鶴png）は暫定。migrate-to-gnome 8.3で確定する。
- AppImageからのEDS（ホストD-Bus）・位置ポータル・印刷の実機確認は未着手（タスク2.2・4.1・4.2）。

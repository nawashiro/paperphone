"""AppImage配布のための1手順ビルド。

ホストではコンテナエンジンでUbuntu 22.04を起動して再帰実行する。
コンテナ内ではGTK 4.14以上とlibadwaitaをソースから1つのプレフィックスへ組み、
requirements.txt通りのwheelをPython 3.10のvenvに置き、`meson install --destdir`
の出力をAppDirへ組み替えてappimagetoolで固める。再実行は同じ構成のAppImageを作る。

podman run --rm -it -v "$PWD":/src:Z ubuntu:22.04 python3 /src/tools/build_appimage.py --inside
"""

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
BUILD = REPO / "build" / "appimage"
PREFIX = BUILD / "prefix"
APPDIR = BUILD / "AppDir"
STAGE = REPO / "build" / "stage"
APPSTAGE = REPO / "build" / "appstage"
VENV = PREFIX / "python"

GLIB = "2.84.4"
CAIRO = "1.18.4"
PANGO = "1.52.2"
GRAPHENE = "1.10.8"
GTK4 = "4.15.6"
LIBADWAITA = "1.5.8"
APPSTREAM = "1.0.4"
GDK_PIXBUF = "2.42.12"
GOBJECT_INTROSPECTION = "1.82.0"

NATIVE_DEPS = (
    "build-essential pkg-config gettext git curl ca-certificates xz-utils "
    "python3 python3-dev python3-venv python3-pip "
    "libffi-dev zlib1g-dev libexpat1-dev libmount-dev libselinux1-dev libpcre2-dev "
    "libfreetype-dev libfontconfig1-dev libpng-dev libpixman-1-dev libxml2-dev bison flex "
    "libx11-dev libxext-dev libxrandr-dev libxi-dev libxcursor-dev libxdamage-dev "
    "libxinerama-dev libxkbcommon-dev libxcomposite-dev libwayland-dev "
    "wayland-protocols libwayland-bin libdrm-dev libgbm-dev libegl1-mesa-dev libepoxy-dev "
    "libjpeg-dev libtiff-dev libwebp-dev cmake gobject-introspection libgirepository1.0-dev "
    "gir1.2-glib-2.0 gir1.2-poppler-0.18 libpoppler-glib-dev poppler-data "
    "libyaml-dev libcurl4-openssl-dev liblzma-dev libzstd-dev libmagic-dev gperf itstool xsltproc docbook-xsl "
    "fonts-noto-cjk shared-mime-info desktop-file-utils file"
)

STACK = (
    ("glib", GLIB,
     "https://download.gnome.org/sources/glib/2.84/glib-2.84.4.tar.xz"),
    ("gobject-introspection", GOBJECT_INTROSPECTION,
     "https://download.gnome.org/sources/gobject-introspection/1.82/"
     "gobject-introspection-1.82.0.tar.xz"),
    ("libxmlb", "0.3.21",
     "https://github.com/hughsie/libxmlb/archive/refs/tags/0.3.21.tar.gz"),
    ("appstream", APPSTREAM,
     "https://github.com/ximion/appstream/archive/refs/tags/v1.0.4.tar.gz"),
    ("cairo", CAIRO,
     "https://www.cairographics.org/releases/cairo-1.18.4.tar.xz"),
    ("graphene", GRAPHENE,
     "https://download.gnome.org/sources/graphene/1.10/graphene-1.10.8.tar.xz"),
    ("pango", PANGO,
     "https://download.gnome.org/sources/pango/1.52/pango-1.52.2.tar.xz"),
    ("gdk-pixbuf", GDK_PIXBUF,
     "https://download.gnome.org/sources/gdk-pixbuf/2.42/gdk-pixbuf-2.42.12.tar.xz"),
    ("gtk", GTK4,
     "https://download.gnome.org/sources/gtk/4.15/gtk-4.15.6.tar.xz"),
    ("libadwaita", LIBADWAITA,
     "https://download.gnome.org/sources/libadwaita/1.5/libadwaita-1.5.8.tar.xz"),
)

SKIP = {
    # glib単体ではintrospectionを組み、scannerはgobject-introspectionで作る。
    "glib": ["introspection=disabled"],
    "gobject-introspection": [],
    # Ubuntu 22.04のlibxmlb 0.3.10はappstream 1.0.4の要求(>=0.3.14)に届かない。
    "libxmlb": ["introspection=false", "gtkdoc=false", "tests=false",
                "cli=false"],
    # libadwaita 1.5.8が同梱するappstream.wrapはrevision=mainの可変先で、
    # 現行mainはlibadwaitaが渡すsvg-supportオプションを失っている。
    # 足りない分はlibxmlbと共に上のSTACKで固定版を組み、pkg-config経路で
    # 解決させるため同梱subprojectには落ちない。
    "appstream": [
        "systemd=false", "apidocs=false", "install-docs=false",
        "stemming=false", "svg-support=false", "gir=false",
    ],
    "cairo": [],
    "graphene": ["introspection=enabled"],
    "pango": ["introspection=enabled"],
    "gdk-pixbuf": ["introspection=enabled", "man=false"],
    "gtk": [
        "introspection=enabled",
        "media-gstreamer=disabled",
        "build-tests=false",
        # Ubuntu 22.04のwayland 1.20/wayland-protocols 1.25はGTK 4.15の要求
        # (>=1.21/>=1.36)に届かず、GTK同梱のwayland 1.21 subprojectは
        # wayland-protocolsのtestsが足すwayland-scanner dependencyを提供しない。
        # wayland backendを切ってX11のみから出す。GNOME Waylandセッションでは
        # XWayland経路で動く。
        "wayland-backend=false",
        # Vulkanデバイス・ヘッダをAppImageに同梱する宛てがないため、
        # 描写はNGL(GL 3.2+)経路に限る。
        "vulkan=disabled",
    ],
    "libadwaita": ["introspection=enabled", "tests=false", "examples=false",
                   "vapi=false"],
}


def run(command, cwd=None, update=True):
    built = stack_env()
    env = {**os.environ, **built} if update else os.environ
    print("+", " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], check=True, env=env, cwd=cwd)


def stack_env():
    lib = PREFIX / "lib" / "x86_64-linux-gnu"
    return {
        "PATH": f"{PREFIX}/bin:{os.environ.get('PATH', '')}",
        "LD_LIBRARY_PATH": f"{lib}:{os.environ.get('LD_LIBRARY_PATH', '')}",
        "GI_TYPELIB_PATH": f"{lib}/girepository-1.0:/usr/lib/x86_64-linux-gnu/girepository-1.0",
        "PKG_CONFIG_PATH": f"{lib}/pkgconfig:/usr/lib/x86_64-linux-gnu/pkgconfig",
    }


def in_jammy():
    release = Path("/etc/os-release")
    if not release.is_file():
        return False
    values = dict(line.split("=", 1) for line in release.read_text().splitlines() if "=" in line)
    identity = values.get("ID", "").strip('"')
    version = values.get("VERSION_ID", "").strip('"')
    return identity == "ubuntu" and version == "22.04"


def apt():
    run(["apt-get", "update"], update=False)
    run(["bash", "-c", "DEBIAN_FRONTEND=noninteractive apt-get install -yq " + NATIVE_DEPS],
        update=False)
    # glib 2.84はUbuntu 22.04同梱のmeson 0.61より新しいmesonを要求する。
    run(["python3", "-m", "pip", "install", "--quiet", "meson==1.6.1", "ninja==1.13.0"],
        update=False)


def build_stack():
    sources = BUILD / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    for name, version, url in STACK:
        if (sources / f"{name}-{version}" / ".stamp").is_file():
            continue
        archive = sources / url.rsplit("/", 1)[-1]
        if not archive.is_file():
            run(["curl", "-L", "-o", archive, url], cwd=sources, update=False)
        if not (sources / f"{name}-{version}").is_dir():
            run(["tar", "-xf", archive, "-C", sources], update=False)
        options = [f"--prefix={PREFIX}"] + [f"-D{option}" for option in SKIP[name]]
        run(["meson", "setup", *options, "bld"],
            cwd=sources / f"{name}-{version}", update=True)
        run(["ninja", "install"], cwd=sources / f"{name}-{version}" / "bld", update=True)
        (sources / f"{name}-{version}" / ".stamp").touch()


def python_bundle():
    venv = VENV
    if (venv / ".stamp").is_file():
        return
    run(["python3", "-m", "venv", str(venv)], update=False)
    python = venv / "bin" / "python3"
    run([python, "-m", "pip", "install", "-r", REPO / "requirements.txt"], update=False)
    # pipのビルド分離はPKG_CONFIG_PATHを担持子に渡さないため、mesonpyをvenvに
    # 入れて分離なしで組み、同梱プレフィックスのGI/cairoに結合させる。
    run([python, "-m", "pip", "install", "meson", "meson-python", "ninja"], update=False)
    run([python, "-m", "pip", "install", "--no-build-isolation", "pycairo"], update=True)
    # PyGObjectは目標のPythonにpycairoが入った状態でビルドする必要がある
    # (gi/pygi-foreign-cairo.cがpy3cairo.hを必要とする)。
    run([python, "-m", "pip", "install", "--no-build-isolation", "PyGObject"], update=True)
    (venv / ".stamp").touch()


def collect_appdir():
    shutil.rmtree(APPSTAGE, ignore_errors=True)
    shutil.rmtree(STAGE, ignore_errors=True)
    shutil.rmtree(APPDIR, ignore_errors=True)    # メタデータ前の構成時python(pip install先)はvenvのsite-packagesを
    # 見ず、GI同梱の読み込み試験も同pythonで走る。LD_LIBRARY_PATHはstack_env
    # が同梱プレフィックスを先に置くためライブラリはそちらへ解決される。
    site = str(VENV / "lib" / "python3.10" / "site-packages")
    old_path = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = site if not old_path else f"{site}:{old_path}"
    run(["meson", "setup", "--prefix=/usr",
         "--python.purelibdir=/usr/lib/python3/dist-packages", APPSTAGE],
        cwd=REPO, update=True)
    run(["meson", "install", "--destdir", STAGE], cwd=APPSTAGE, update=True)

    APPDIR.mkdir(parents=True)
    stage_root = STAGE / "usr"
    for name in ("bin", "lib", "share", "libexec"):
        source = stage_root / name
        if source.is_dir():
            shutil.copytree(source, APPDIR / name, symlinks=True, dirs_exist_ok=True)
    for name in ("lib/x86_64-linux-gnu",):
        prefix_item = PREFIX / name
        if prefix_item.is_dir():
            shutil.copytree(prefix_item, APPDIR / name, symlinks=True, dirs_exist_ok=True)
    python_lib = PREFIX / "python" / "lib"
    if python_lib.is_dir():
        shutil.copytree(python_lib, APPDIR / "lib", symlinks=True, dirs_exist_ok=True)
    # Python解釈系(3.10)と基盤stdlibを同梱。クリーン環境のpython3は宛てにならない。
    Path(APPDIR / "usr" / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy2("/usr/bin/python3.10", APPDIR / "usr" / "bin" / "python3.10")
    shutil.copytree("/usr/lib/python3.10", APPDIR / "usr" / "lib" / "python3.10",
                    symlinks=True, dirs_exist_ok=True)
    etc_certs = Path("/etc/ssl/certs")
    if etc_certs.is_dir():
        shutil.copytree(etc_certs, APPDIR / "etc" / "ssl" / "certs", symlinks=True,
                        dirs_exist_ok=True)
    girepo = APPDIR / "lib" / "x86_64-linux-gnu" / "girepository-1.0"
    girepo.mkdir(parents=True, exist_ok=True)
    system_girepo = Path("/usr/lib/x86_64-linux-gnu/girepository-1.0")
    for name in ("Poppler-0.18.typelib", "GdkPixbuf-2.0.typelib", "GLib-2.0.typelib",
                 "GObject-2.0.typelib", "GModule-2.0.typelib", "Gio-2.0.typelib",
                 "GioUnix-2.0.typelib"):
        source = system_girepo / name
        if source.is_file():
            shutil.copy2(source, girepo / name)
    schemas = PREFIX / "share" / "glib-2.0" / "schemas"
    if schemas.is_dir():
        shutil.copytree(schemas, APPDIR / "share" / "glib-2.0" / "schemas",
                        symlinks=True, dirs_exist_ok=True)
    # appimagetoolはdesktop・Iconを実AppDir直下で探す(root直下一覧のみ)。
    app_id = "dev.nawashiro.paperphone"
    shutil.copy2(STAGE / "usr/share/applications" / f"{app_id}.desktop",
                 APPDIR / f"{app_id}.desktop")
    shutil.copy2(STAGE / "usr/share/icons/hicolor/192x192/apps" / f"{app_id}.png",
                 APPDIR / f"{app_id}.png")


def bundle_runtime_libs():
    """AppDir内のELFが参照する共有ライブラリで、GLib基幹以外を同梱する。

    glibc基幹（libc・ld-linux等）はホストの22.04に任せる。
    """
    arch = "x86_64-linux-gnu"
    appdir_lib = APPDIR / "lib" / arch
    keep_out = re.compile(
        r"^(lib(c|\.c|c\.so\.6|m|m\.so\.6|pthread|pthread\.so\.0|dl|dl\.so\.2|rt|rt\.so\.1"
        r"|resolv\.so\.2|nsl\.so\.1)\.so|ld-linux|libgcc_s)")

    def elf_files():
        for path in APPDIR.rglob("*"):
            if path.suffix not in (".so",) and ".so." not in path.name and not path.is_file():
                continue
            try:
                with open(path, "rb") as handle:
                    if handle.read(4) == b"\x7fELF":
                        yield path
            except OSError:
                continue

    seen = set()
    for round_index in range(4):
        copied = 0
        for elf in list(elf_files()):
            probe = run_capture(["ldd", elf])
            for line in probe.splitlines():
                if "=>" not in line or "not found" in line:
                    continue
                source_text = line.split("=>", 1)[1].strip().split(" ", 1)[0]
                source = Path(source_text)
                name = source.name
                if not source.is_file() or name in seen:
                    continue
                if keep_out.match(name):
                    seen.add(name)
                    continue
                shutil.copy2(source, appdir_lib / name)
                seen.add(name)
                copied += 1
        if copied == 0:
            break


def run_capture(command, update=False):
    baked = stack_env()
    result = subprocess.run([str(part) for part in command], env={**os.environ, **baked},
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    return result.stdout.decode("utf-8", "replace")


def write_apprun():
    """同梱プレフィックスと同梱Python(3.10)だけを参照し、ホストに委ねない。"""
    launcher = APPDIR / "AppRun"
    launcher.write_text(
        "#!/bin/sh\n"
        'HERE="$(dirname "$(readlink -f "$0")")"\n'
        'ARCH="$(uname -m)-linux-gnu"\n'
        'export LD_LIBRARY_PATH="$HERE/lib/$ARCH"\n'
        'export GI_TYPELIB_PATH="$HERE/lib/$ARCH/girepository-1.0"\n'
        'export GIO_MODULE_DIR="$HERE/lib/$ARCH/gio/modules"\n'
        'export XDG_DATA_DIRS="$HERE/share:$XDG_DATA_DIRS"\n'
        'export SSL_CERT_DIR="$HERE/etc/ssl/certs"\n'
        'export PYTHONHOME="$HERE/usr"\n'
        'export PYTHONPATH="$HERE/lib/python3/dist-packages:'
        '$HERE/lib/python3.10/site-packages"\n'
        'exec "$HERE/usr/bin/paperphone" "$@"\n')
    launcher.chmod(0o755)

    wrapper = APPDIR / "usr" / "bin" / "paperphone"
    wrapper.write_text(
        "#!/bin/sh\n"
        'exec "$(dirname "$0")/python3.10" -m paperphone "$@"\n')
    wrapper.chmod(0o755)


def package():
    tool = BUILD / "appimagetool"
    if not tool.is_file():
        run(["curl", "-L", "-o", tool,
             "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"],
            update=False)
    tool.chmod(0o755)
    suffix = os.environ.get("APPIMAGE_SUFFIX", "release")
    image = BUILD / f"paperphone-{suffix}.AppImage"
    run([tool, "--appimage-extract-and-run", APPDIR, image], update=False)
    print("+", image, image.stat().st_size, "bytes")


def main(arguments):
    if "--inside" in arguments or in_jammy():
        BUILD.mkdir(parents=True, exist_ok=True)
        apt()
        build_stack()
        python_bundle()
        collect_appdir()
        bundle_runtime_libs()
        write_apprun()
        package()
    else:
        engine = os.environ.get("CONTAINER_ENGINE", "podman")
        bootstrap = (
            "apt-get update && "
            "DEBIAN_FRONTEND=noninteractive apt-get install -yq python3 "
            "&& exec python3 /src/tools/build_appimage.py --inside")
        run([engine, "run", "--rm", "-v", f"{REPO}:/src:Z",
             "ubuntu:22.04", "/bin/sh", "-c", bootstrap], cwd=REPO)


if __name__ == "__main__":
    main(sys.argv[1:])


if __name__ == "__main__":
    main(sys.argv[1:])

"""AppImage配布のための1手順ビルド。

ホストではコンテナエンジンでUbuntu 22.04を起動して再帰実行する。
コンテナ内ではGTK 4.14以上とlibadwaitaをソースから1つのプレフィックスへ組み、
requirements.txt通りのwheelをPython 3.10のvenvに置き、`meson install --destdir`
の出力をAppDirへ組み替えてappimagetoolで固める。再実行は同じ構成のAppImageを作る。

podman run --rm -it -v "$PWD":/src:Z ubuntu:22.04 /src/tools/build_appimage.sh --inside
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
BUILD = REPO / "build" / "appimage"
PREFIX = BUILD / "prefix"
APPDIR = BUILD / "AppDir"
STAGE = REPO / "build" / "stage"

GLIB = "2.84.4"
CAIRO = "1.18.4"
PANGO = "1.52.2"
GRAPHENE = "1.10.8"
GTK4 = "4.15.6"
LIBADWAITA = "1.5.8"

NATIVE_DEPS = (
    "build-essential meson ninja-build pkg-config gettext git curl ca-certificates xz-utils "
    "python3 python3-dev python3-venv python3-pip "
    "libffi-dev zlib1g-dev libexpat1-dev libmount-dev libselinux1-dev "
    "libfreetype-dev libfontconfig1-dev libpng-dev libpixman-1-dev "
    "libx11-dev libxext-dev libxrandr-dev libxi-dev libxcursor-dev libxdamage-dev "
    "libxinerama-dev libxkbcommon-dev libxcomposite-dev libwayland-dev "
    "wayland-protocols libdrm-dev libgbm-dev libegl1-mesa-dev libepoxy-dev "
    "libjpeg-dev libtiff-dev gobject-introspection libgirepository1.0-dev "
    "shared-mime-info desktop-file-utils"
)

STACK = (
    ("glib", GLIB,
     "https://download.gnome.org/sources/glib/2.84/glib-2.84.4.tar.xz"),
    ("cairo", CAIRO,
     "https://download.gnome.org/sources/cairo/1.18/cairo-1.18.4.tar.xz"),
    ("graphene", GRAPHENE,
     "https://download.gnome.org/sources/graphene/1.10/graphene-1.10.8.tar.xz"),
    ("gtk", GTK4,
     "https://download.gnome.org/sources/gtk/4.15/gtk-4.15.6.tar.xz"),
    ("pango", PANGO,
     "https://download.gnome.org/sources/pango/1.52/pango-1.52.2.tar.xz"),
    ("libadwaita", LIBADWAITA,
     "https://download.gnome.org/sources/libadwaita/1.5/libadwaita-1.5.8.tar.xz"),
)

SKIP = {
    "glib": ["introspection=enabled"],
    "cairo": ["introspection=enabled"],
    "graphene": ["introspection=enabled", "tests=false"],
    "gtk": ["introspection=enabled", "tests=false", "demos=false",
            "build-tests=false"],
    "pango": ["introspection=enabled", "tests=false"],
    "libadwaita": ["introspection=enabled", "tests=false", "examples=false"],
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
            cwd=sources / f"{name}-{version}", update=False)
        run(["ninja", "install"], cwd=sources / f"{name}-{version}" / "bld", update=False)
        (sources / f"{name}-{version}" / ".stamp").touch()


def python_bundle():
    venv = PREFIX / "python"
    if (venv / ".stamp").is_file():
        return
    run(["python3", "-m", "venv", str(venv)], update=False)
    python = venv / "bin" / "python3"
    run([python, "-m", "pip", "install", "-r", REPO / "requirements.txt"], update=False)
    run([python, "-m", "pip", "install", "pycairo", "PyGObject"], update=False)
    (venv / ".stamp").touch()


def collect_appdir():
    shutil.rmtree(STAGE, ignore_errors=True)
    run(["meson", "setup", f"--python.purelibdir=/usr/lib/python3/dist-packages",
         "--prefix=/usr", STAGE], update=True)
    run(["ninja", "install"], cwd=STAGE, update=True)

    shutil.rmtree(APPDIR, ignore_errors=True)
    APPDIR.mkdir(parents=True)
    stage_root = STAGE / "usr"
    for name in ("bin", "lib", "share", "libexec"):
        source = stage_root / name
        if source.is_dir():
            shutil.copytree(source, APPDIR / name, symlinks=True, dirs_exist_ok=True)
    for name in ("lib", "lib/x86_64-linux-gnu"):
        prefix_lib = PREFIX / name
        if prefix_lib.is_dir():
            shutil.copytree(prefix_lib, APPDIR / name, symlinks=True, dirs_exist_ok=True)
    PYTHON_PATH = PREFIX / "python" / "lib"
    if PYTHON_PATH.is_dir():
        shutil.copytree(PYTHON_PATH, APPDIR / "lib", symlinks=True, dirs_exist_ok=True)
    resx = (APPDIR / "lib" / "x86_64-linux-gnu" / "girepository-1.0")
    resx.mkdir(parents=True, exist_ok=True)
    run(["glib-compile-schemas", APPDIR / "usr" / "share" / "glib-2.0" / "schemas"],
        update=True)
    # AppRunは同梱プレフィックスのldに従い、GIとlocaleをこのAppDir内へ向ける。
    launcher = APPDIR / "AppRun"
    launcher.write_text(
        "#!/bin/sh\n"
        'HERE="$(dirname "$(readlink -f "$0")")"\n'
        'export LD_LIBRARY_PATH="$HERE/lib/x86_64-linux-gnu"\n'
        'export GI_TYPELIB_PATH="$HERE/lib/x86_64-linux-gnu/girepository-1.0"\n'
        'export GIO_MODULE_DIR="$HERE/lib/x86_64-linux-gnu/gio/modules"\n'
        'export XDG_DATA_DIRS="$HERE/share:$XDG_DATA_DIRS"\n'
        'exec "$HERE/bin/paperphone" "$@"\n')
    launcher.chmod(0o755)
    stamps = BUILD / "tools"
    shutil.rmtree(stamps, ignore_errors=True)
    if stamps.parent.is_dir():
        run(["curl", "-L", "-o", BUILD / "appimagetool.zip",
             "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"])
        os.chmod(BUILD / "appimagetool.zip", 0o755)
        run([BUILD / "appimagetool.zip", "--appimage-extract-and-run", APPDIR, "-o",
             BUILD / f"paperphone-{os.environ.get('APPIMAGE_SUFFIX', 'release')}.AppImage"],
            update=False)
        image = BUILD / f"paperphone-{os.environ.get('APPIMAGE_SUFFIX', 'release')}.AppImage"
        print("+", image, image.stat().st_size, "bytes")


def main(arguments):
    if "--inside" in arguments or in_jammy():
        BUILD.mkdir(parents=True, exist_ok=True)
        apt()
        build_stack()
        python_bundle()
        collect_appdir()
    else:
        engine = os.environ.get("CONTAINER_ENGINE", "podman")
        run([engine, "run", "--rm", "-it", "-v", f"{REPO}:/src:Z",
             "ubuntu:22.04", "/src/tools/build_appimage.py", "--inside"], cwd=REPO)


if __name__ == "__main__":
    main(sys.argv[1:])

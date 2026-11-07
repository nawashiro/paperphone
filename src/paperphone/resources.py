"""冊子に使う既存画像・フォント素材をパッケージから読む。"""

from __future__ import annotations

from importlib.resources import files

PAPER_APP_ASSETS = (
    "conversion_charts",
    "knots",
    "italian_phrasebook",
    "sign_language",
    "multiplication_table",
    "recipe",
    "pigeon_origami",
    "sudoku",
    "maze",
    "word_of_the_day",
    "anagram",
    "riddle",
    "typography",
)
FONT_ASSETS = ("varela_regular.otf", "worksans_regular.ttf")


def N_(message: str) -> str:
    """gettext抽出対象のメッセージを示す恒等関数。"""
    return message


def paper_app_names(translate=None) -> tuple[str, ...]:
    """元アプリの順序で紙アプリ名を返す。"""
    if translate is None:
        from .i18n import gettext_for

        translate = gettext_for()
    return (
        translate(N_("Conversion Charts")),
        translate(N_("Knots")),
        translate(N_("Italian Phrasebook")),
        translate(N_("Sign Language")),
        translate(N_("Multiplication Table")),
        translate(N_("Recipe")),
        translate(N_("Origami Pigeon")),
        translate(N_("Sudoku")),
        translate(N_("Maze")),
        translate(N_("Word of the Day")),
        translate(N_("Anagram")),
        translate(N_("Riddle")),
        translate(N_("Typography")),
    )


def read_paper_app(name: str) -> bytes:
    """紙アプリ画像をPNGバイト列で返す。任意パスは受け付けない。"""
    if name not in PAPER_APP_ASSETS:
        raise ValueError(f"未知の紙アプリ素材です: {name}")
    return files("paperphone").joinpath(
        "resources", "paper-apps", f"{name}.png"
    ).read_bytes()


def read_font(name: str) -> bytes:
    """同梱フォントをバイト列で返す。"""
    if name not in FONT_ASSETS:
        raise ValueError(f"未知のフォント素材です: {name}")
    return files("paperphone").joinpath("resources", "fonts", name).read_bytes()

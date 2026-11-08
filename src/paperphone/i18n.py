"""gettextによる言語選択と翻訳読込。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import gettext
import locale
import os
from importlib.resources import files

DOMAIN = "paperphone"
_generation_language = ContextVar("paperphone_generation_language", default=None)


def language_code(language: str | None = None) -> str:
    """環境設定または明示値を英語／日本語の2文字コードに正規化する。"""
    requested = language or _generation_language.get() or os.environ.get("PAPERPHONE_LANGUAGE")
    if requested is None:
        requested = (
            os.environ.get("LANGUAGE")
            or os.environ.get("LC_ALL")
            or os.environ.get("LC_MESSAGES")
            or os.environ.get("LANG")
        )
    if requested is None:
        try:
            requested = locale.getlocale(locale.LC_MESSAGES)[0]
        except (AttributeError, ValueError):
            requested = None

    first_language = (requested or "en").split(":", 1)[0]
    primary = first_language.split(".", 1)[0].replace("_", "-").lower().split("-", 1)[0]
    return "ja" if primary == "ja" else "en"


def N_(message: str) -> str:
    """遅延翻訳する固定msgidの抽出マーカー。ここでは翻訳しない。"""
    return message


def set_language(language: str):
    """表示言語だけをメモリ内で切り替える。OSのlocaleや個人データを保存しない。"""
    os.environ["PAPERPHONE_LANGUAGE"] = language_code(language)


@contextmanager
def using_language(language: str):
    """ワーカーの生成全体を捕捉済み言語に固定し、UI切替と混在させない。"""
    token = _generation_language.set(language_code(language))
    try:
        yield
    finally:
        _generation_language.reset(token)


def gettext_for(language: str | None = None):
    """省略時は表示/生成コンテキストに追従、明示時は固定言語の関数を返す。"""
    if language is None:
        return lambda message: gettext_for(language_code())(message)
    localedir = os.environ.get("PAPERPHONE_LOCALEDIR")
    if localedir is None:
        localedir = str(files("paperphone").joinpath("resources", "locale"))
    selected = language_code(language)
    return gettext.translation(
        DOMAIN,
        localedir=localedir,
        languages=[selected],
        fallback=True,
    ).gettext

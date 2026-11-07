"""gettextによる言語選択と翻訳読込。"""

from __future__ import annotations

import gettext
import locale
import os
from importlib.resources import files

DOMAIN = "paperphone"


def language_code(language: str | None = None) -> str:
    """環境設定または明示値を英語／日本語の2文字コードに正規化する。"""
    requested = language or os.environ.get("PAPERPHONE_LANGUAGE")
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


def gettext_for(language: str | None = None):
    """指定言語またはシステムロケールに対応するgettext関数を返す。"""
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

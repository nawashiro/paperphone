"""同梱OFLフォントをプロセスのFontconfigへ登録する。設定・キャッシュは書かない。"""

from functools import lru_cache
from importlib.resources import files
import ctypes
import ctypes.util


@lru_cache(maxsize=1)
def register_printing_fonts() -> bool:
    """既存フォントファイルをメモリ内アプリフォントとして登録する。

    Falseなら登録失敗。呼出側は中断または警告し、原字体の再現成功と扱わない。
    登録成功後の日本語はPangoのシステムフォールバックを使う。
    個人データや一時ファイルを扱わず、FcConfigBuildFonts/キャッシュ保存は呼ばない。
    """
    library = ctypes.util.find_library("fontconfig")
    if not library:
        return False
    try:
        fc = ctypes.CDLL(library)
        fc.FcConfigGetCurrent.restype = ctypes.c_void_p
        fc.FcConfigAppFontAddFile.argtypes = (ctypes.c_void_p, ctypes.c_char_p)
        fc.FcConfigAppFontAddFile.restype = ctypes.c_int
        config = fc.FcConfigGetCurrent()
        if not config:
            return False
        root = files("paperphone").joinpath("resources", "fonts")
        success = all(fc.FcConfigAppFontAddFile(config, str(root.joinpath(name)).encode())
                      for name in ("varela_regular.otf", "worksans_regular.ttf"))
        if success:
            import gi
            gi.require_version("PangoCairo", "1.0")
            gi.require_version("PangoFc", "1.0")
            from gi.repository import PangoCairo, PangoFc
            font_map = PangoCairo.FontMap.get_default()
            # changed()だけでは、先にPangoを使ったプロセスのFcフォントセットが
            # 無効化されない。既存画面・他の紙面生成後の登録でも反映させる。
            if isinstance(font_map, PangoFc.FontMap):
                font_map.config_changed()
            else:
                font_map.changed()
        return success
    except (OSError, AttributeError):
        return False


VARELA = "Varela, Noto Sans CJK JP, sans-serif"
WORK_SANS = "Work Sans, Noto Sans CJK JP, sans-serif"

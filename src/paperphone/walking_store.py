"""県全域の徒歩グラフを版ごとに保存するキャッシュ。検索語・座標の外部送信はない。

一回の版ごとにPBFを県全域で走査し、出来上がったグラフをそのまま
バージョン名（県＋SHA-256）で保存する。経路の作成はその版の保存済み
グラフから、目的地のBboxだけを残して読めば済み、毎回のPBF走査が不要
になる。版が変われば同名の旧ファイルを掃除する。
"""

import os
import tempfile
from pathlib import Path

import numpy

from .walking import _scan_pedestrian, WalkingError, WalkingRouter

_SCHEMA_VERSION = 1


def graph_path(prefecture: int, sha256: str, *, directory=None):
    """版（県・SHA-256）に対応した保存先。"""
    if type(prefecture) is not int or not 1 <= prefecture <= 47 or not sha256 or not isinstance(sha256, str):
        raise WalkingError("invalid-input")
    root = (Path(directory) if directory is not None else
            Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "paperphone" / "pedestrian")
    return root / f"JP-{prefecture:02d}-{sha256}.walk.npz"


def build_full_graph(path, *, cancelled=None):
    """県全域（Bboxで絞らない）で徒歩グラフを作る。重いI/Oはワーカーから。"""
    try:
        return _scan_pedestrian(path, inside=lambda point: True, cancelled=cancelled)
    except WalkingError:
        raise
    except Exception:
        raise WalkingError("invalid-data") from None


def save_graph(path, sha256: str, prefecture: int, node_ids, coordinates, edges):
    """版付きで原子的に保存する。破損時は検証で検知し、再作成で回復する。"""
    try:
        payload = {
            "schema": numpy.array([_SCHEMA_VERSION], dtype=numpy.int32),
            "prefecture": numpy.array([prefecture], dtype=numpy.int32),
            "node_ids": numpy.asarray(node_ids, dtype=numpy.int64),
            "coordinates": numpy.asarray(coordinates, dtype=numpy.float64),
            "edges": numpy.asarray(edges, dtype=numpy.int64),
        }
        if payload["coordinates"].ndim != 2 or payload["coordinates"].shape[1] != 2:
            raise ValueError
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".walk-")
        with os.fdopen(fd, "wb") as stream:
            numpy.savez(stream, **payload)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        prefix = f"JP-{prefecture:02d}-"
        for stale in path.parent.glob(prefix + "*.walk.npz"):
            if stale != path:
                try:
                    stale.unlink()
                except OSError:
                    pass
        return True
    except (OSError, ValueError, TypeError):
        try:
            os.unlink(temporary)
        except (OSError, UnboundLocalError):
            pass
        return False


def load_graph(path, sha256: str, prefecture: int):
    """版が一致したグラフ配列を返す。不一致・破損・旧書式はNone。"""
    try:
        with numpy.load(path, allow_pickle=False) as payload:
            version = payload["schema"].tolist()
            saved_prefecture = payload["prefecture"].tolist()
            node_ids = payload["node_ids"]
            coordinates = payload["coordinates"]
            edges = payload["edges"]
        if (version != [_SCHEMA_VERSION] or saved_prefecture != [prefecture]
                or node_ids.ndim != 1 or coordinates.shape != (node_ids.size, 2)
                or edges.ndim != 2 or edges.shape[1] != 2
                or node_ids.size and not numpy.isfinite(coordinates).all()):
            return None
        return node_ids, coordinates, edges
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _router_from_arrays(node_ids, coordinates, edges, bounds, max_snap_m=100.0):
    """保存済みグラフから、経路のBboxだけを残してルーターを作る。"""
    inside = numpy.array([_belongs(point, bounds) for point in coordinates.tolist()])
    if not inside.any():
        raise WalkingError("no-graph")
    kept_ids = node_ids[inside]
    kept = {int(node_id): tuple(point) for node_id, point in zip(kept_ids.tolist(), coordinates[inside].tolist())}
    members = set(kept)
    pairs = [(int(source), int(target)) for source, target in edges.tolist()
             if int(source) in members and int(target) in members]
    return WalkingRouter(kept, pairs, bounds=bounds, max_snap_m=max_snap_m)


def _belongs(point, bounds):
    from .walking import _inside
    return _inside(point, bounds)

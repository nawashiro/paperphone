"""movisda extractsと一県インデックスの実証。個人データは含まれない(OSM公開データ)。

.venv-geocoding/bin/python tools/probe_osm.py --mode download --prefecture 13 --out build/cache/tokyo-latest.osm.pbf --state build/cache/tokyo-state.json
.venv-geocoding/bin/python tools/probe_osm.py --mode scan --path build/cache/tokyo-latest.osm.pbf

--mode download は前回状態(ETag・Last-Modified)から条件付き要求を組み、
304なら再ダウンロードしない。--mode scan はpyosmiumで1回走査して
地名・種別・座標インデックスの試作を作り、所要時間と規模を集計する。
出力は集合値のみで、経路のURL・例外詳細は伏せる。自動再試行はしない。
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from paperphone.osm_extracts import (  # noqa: E402
    INITIAL,
    RELOADED,
    UNCHANGED,
    ExtractChecker,
    ExtractState,
    ExtractsError,
    prefecture_url,
)

USER_AGENT = "paperphone-osm-probe/0.1"
MAX_DOWNLOAD_BYTES = 2_000_000_000


def _stream_get(url: str, headers: dict[str, str], out: Path, max_bytes: int) -> tuple[int, dict]:
    """HTTPS直取得。ストリームでファイルに書き、応答ヘッダーを返す。"""
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()))
    try:
        stream = opener.open(Request(url, headers=headers), timeout=120)
    except HTTPError as error:
        response = error
    else:
        response = stream
    headers_out = {k.lower(): v for k, v in response.headers.items()}
    status = response.code
    if status == 200:
        written = 0
        with response, out.open("wb") as sink:
            while chunk := response.read(1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    sink.close()
                    out.unlink(missing_ok=True)
                    raise ExtractsError("応答サイズが上限を超えました。")
                sink.write(chunk)
        headers_out["x-probe-written-bytes"] = str(written)
    else:
        response.close()
    return status, headers_out


def mode_download(args: argparse.Namespace) -> int:
    state = ExtractState()
    if args.state and args.state.exists():
        saved = json.loads(args.state.read_text(encoding="utf-8"))
        state = ExtractState(etag=saved.get("etag"), last_modified=saved.get("last_modified"))
    checker = ExtractChecker(state)
    plan = checker.plan({"User-Agent": USER_AGENT})
    url = prefecture_url(args.prefecture)
    print(f"mode=download; plan={plan.action}; conditional={plan.conditional}")
    started = time.perf_counter()
    try:
        status, headers = _stream_get(url, plan.headers, args.out, MAX_DOWNLOAD_BYTES)
    except ExtractsError as error:
        print(f"failed_stage=download; error={error.args[0]}; automatic_retry=False")
        return 1
    elapsed = time.perf_counter() - started
    try:
        outcome, after = checker.observe(status, headers)
    except ExtractsError as error:
        print(f"failed_stage=validate; error={error.args[0]}")
        return 1
    if outcome == UNCHANGED:
        if args.out.exists():
            print("outcome=unchanged; kept_existing_file=True")
    else:
        print("outcome=reloaded; saved_file=True")
    print(f"status={status}; seconds={elapsed:.1f}")
    print(f"etag={after.state.etag}; last_modified={after.state.last_modified}")
    print(f"bytes={after.state.bytes_count}; filename={after.state.filename}")
    if args.state is not None and outcome in (UNCHANGED, RELOADED):
        args.state.write_text(json.dumps(after.state.as_dict()), encoding="utf-8")
        print("state_saved=True")
    return 0


def mode_meta(args: argparse.Namespace) -> int:
    url = prefecture_url(args.prefecture)
    request = Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()))
    try:
        stream = opener.open(request, timeout=60)
    except HTTPError as error:
        stream = error
    meta = {k.lower(): v for k, v in stream.headers.items()}
    print(f"status={stream.code}; etag={meta.get('etag')}; last_modified={meta.get('last-modified')}")
    print(f"bytes={meta.get('content-length')}; filename_present={bool(meta.get('content-disposition'))}")
    print(f"conditions_ok={stream.code in (200, 304)}")
    return 0 if stream.code in (200, 304) else 1


def category(tags) -> str:
    t = dict(tags)
    for key in ("amenity", "shop", "office", "tourism", "leisure", "historic",
                "craft", "healthcare", "railway", "public_transport", "aeroway",
                "place", "natural", "man_made", "highway"):
        if key in t:
            return f"{key}={t[key]}"
    return "other"


def mode_scan(args: argparse.Namespace) -> int:
    import osmium
    from rapidfuzz import process as fuzz_process
    from rapidfuzz.fuzz import WRatio

    path = args.path
    bytes_count = path.stat().st_size
    started = time.perf_counter()
    entries: dict[str, tuple[str, float, float]] = {}
    named_way_kinds: dict[str, int] = {}
    named_relation_count = 0
    processor = osmium.FileProcessor(str(path)).with_filter(osmium.filter.KeyFilter("name"))
    # type_str()は小文字で "n"/"w"/"r" を返す(2026-11-08実測)。
    for item in processor:
        kind_of_item = item.type_str()
        if kind_of_item == "w":
            t = dict(item.tags)
            kind = f"highway={t['highway']}" if "highway" in t else category(t)
            named_way_kinds[kind] = named_way_kinds.get(kind, 0) + 1
            continue
        if kind_of_item == "r":
            named_relation_count += 1
            continue
        name = item.tags["name"]
        if name not in entries:
            loc = item.location
            entries[name] = (category(dict(item.tags)), round(loc.lat, 6), round(loc.lon, 6))
    scan_seconds = time.perf_counter() - started
    names = list(entries)
    total_name_bytes = sum(len(name.encode("utf-8")) for name in names)
    index_estimate = total_name_bytes + len(names) * (2 * 24) + sum(len(k.encode("utf-8")) + 16 for k, v in named_way_kinds.items())
    lookup_started = time.perf_counter()
    top = fuzz_process.extract(args.query, names, scorer=WRatio, limit=3, score_cutoff=50)
    lookup_seconds = time.perf_counter() - lookup_started

    print(f"pbf_bytes={bytes_count}; scan_seconds={scan_seconds:.1f}")
    print(f"named_poi_nodes={len(entries)}; distinct_names={len(set(names))}")
    print(f"named_ways_total={sum(named_way_kinds.values())}; named_relations={named_relation_count}")
    highway_ways = sum(v for k, v in named_way_kinds.items() if k.startswith("highway="))
    print(f"named_highway_ways={highway_ways}")
    print(f"name_kinds_top={sorted(named_way_kinds.items(), key=lambda kv: -kv[1])[:5]}")
    print(f"index_estimate_kb={index_estimate // 1024}")
    print(f"fuzzy_query_chars={len(args.query)}; fuzzy_ms={lookup_seconds * 1000:.0f}; fuzzy_matches={len(top)}")

    print(f"steps_replayed=false; full_index_buildable=True")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("download", "meta", "scan"), required=True)
    parser.add_argument("--prefecture", type=int, default=13)
    parser.add_argument("--out", type=Path, default=Path("build/cache/tokyo-latest.osm.pbf"))
    parser.add_argument("--state", type=Path, default=None)
    parser.add_argument("--path", type=Path, default=None, help="scan対象のPBF")
    parser.add_argument("--query", default="新宿", help="曖昧照合の確認に使う公開地名クエリ")
    args = parser.parse_args()
    if args.mode == "download":
        return mode_download(args)
    if args.mode == "meta":
        return mode_meta(args)
    return mode_scan(args)


if __name__ == "__main__":
    raise SystemExit(main())

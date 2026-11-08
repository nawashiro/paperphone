"""都道府県PBFの明示取得・条件付き更新と原子的な保存。座標は扱わない。"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from .osm_extracts import (MAX_EXTRACT_BYTES, ExtractState, ExtractChecker, ExtractsError,
                           RELOADED, prefecture_url)

UTC = timezone.utc


class ExtractFailure(ExtractsError):
    """利用者へ表示する安全な分類。外部URL・例外詳細を保存しない。"""
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class CachedExtract:
    prefecture: int
    path: Path = field(repr=False)
    sha256: str
    state: ExtractState
    basis: datetime


@dataclass(frozen=True)
class ExtractUpdate:
    extract: CachedExtract
    downloaded: bool


class _MirrorRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if (parsed.scheme != "https" or parsed.hostname != "osm.download.movisda.io"
                or parsed.port not in (None, 443) or parsed.username or parsed.password):
            raise ExtractFailure("redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def stream_extract(url, headers):
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()),
                          _MirrorRedirect())
    try:
        return opener.open(Request(url, headers=headers), timeout=120)
    except HTTPError as error:
        return error


def _basis(state, prefecture):
    if state.filename:
        match = re.fullmatch(r"JP-(\d{2})-(\d{12})\.osm\.pbf", state.filename)
        if not match or int(match[1]) != prefecture:
            raise ExtractFailure("invalid")
        try:
            return datetime.strptime(match[2], "%Y%m%d%H%M").replace(tzinfo=UTC)
        except ValueError:
            raise ExtractFailure("invalid") from None
    try:
        date = parsedate_to_datetime(state.last_modified)
        if date.tzinfo:
            return date.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        pass
    raise ExtractFailure("invalid")


def _pbf_header(prefix):
    # Fileformatの最初のBlobHeaderはOSMHeader。完全なOSM幾何の解析は検索側が行う。
    if len(prefix) < 4:
        return False
    length = int.from_bytes(prefix[:4], "big")
    return 0 < length <= 65536 and len(prefix) >= 4 + length and b"OSMHeader" in prefix[4:4 + length]


class ExtractStore:
    """保存先はアプリ専用ディレクトリ。取得はconfirmed=Trueの呼出しだけ。

    版ごとに異なるファイル名を用い、最後に小さなmanifestを原子的に置換する。
    更新前のPBFは消さず、進行中の紙面スナップショットの入力を変えない。
    """
    def __init__(self, directory=None, *, transport=stream_extract,
                 clock=lambda: datetime.now(UTC)):
        self.directory = Path(directory) if directory is not None else (
            Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "paperphone" / "maps")
        self.transport = transport
        self.clock = clock
        self._next_request = {}

    def _manifest(self, prefecture):
        prefecture_url(prefecture)  # 厳密な整数・範囲検査。
        return self.directory / f"JP-{prefecture:02d}.json"

    def delete(self, prefecture):
        """明示した県のmanifestとPBFだけを保存領域から消す。

        カタログ内の保存先だけを見て消すため、同じディレクトリを指さない
        パスや他の県は対象にしない。通信・他のデータへの触れは行わない。
        """
        if type(prefecture) is not int or not 1 <= prefecture <= 47:
            raise ExtractFailure("invalid")
        self._manifest(prefecture)  # 県範囲の検査を共有する。
        try:
            manifest = self._manifest(prefecture)
            if manifest.is_file():
                data = json.loads(manifest.read_text(encoding="utf-8"))
                file_name = f"JP-{prefecture:02d}-{data['sha256']}.osm.pbf"
                data_path = self.directory / file_name
                if data_path.is_file() and not data_path.is_symlink():
                    data_path.unlink()
                # 場所検索・徒歩経路・印刷地物の派生データも同じ県・同じ版に属する。
                derived_path = self.directory / f"JP-{prefecture:02d}-{data['sha256']}.sqlite"
                if derived_path.is_file() and not derived_path.is_symlink():
                    derived_path.unlink()
            else:
                for stored in self.directory.glob(f"JP-{prefecture:02d}-*.osm.pbf"):
                    if stored.is_file() and not stored.is_symlink():
                        stored.unlink()
                for stale in self.directory.glob(f"JP-{prefecture:02d}-*.sqlite"):
                    if stale.is_file() and not stale.is_symlink():
                        stale.unlink()
            if manifest.is_file():
                manifest.unlink()
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise ExtractFailure("storage") from None

    def delete_all(self):
        """アプリ用カタログ内の全県の地図データを消す。カタログ以外は対象にしない。"""
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            for manifest in sorted(self.directory.glob("JP-*.json")):
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                    prefecture = int(data["prefecture"])
                except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                    continue
                self.delete(prefecture)
            for stored in sorted(self.directory.glob("JP-*-*.osm.pbf")):
                if stored.is_file() and not stored.is_symlink():
                    stored.unlink()
            for stale in sorted(self.directory.glob("JP-*-*.sqlite")):
                if stale.is_file() and not stale.is_symlink():
                    stale.unlink()
        except OSError:
            raise ExtractFailure("storage") from None

    def load(self, prefecture):
        """ディスクだけを読み、破損キャッシュを最新として使わない。通信なし。"""
        manifest = self._manifest(prefecture)
        if not manifest.exists():
            return None
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            digest = data["sha256"]
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError
            if data["prefecture"] != prefecture:
                raise ValueError
            path = self.directory / f"JP-{prefecture:02d}-{digest}.osm.pbf"
            state = ExtractState(**data["state"])
            if path.is_symlink() or state.bytes_count != path.stat().st_size:
                raise ValueError
            checksum = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    checksum.update(chunk)
            if checksum.hexdigest() != digest:
                raise ValueError
            return CachedExtract(prefecture, path, digest, state, _basis(state, prefecture))
        except (OSError, ValueError, KeyError, TypeError, ExtractsError):
            raise ExtractFailure("cache") from None

    def update(self, prefecture, *, confirmed=False):
        if not confirmed:
            raise ExtractFailure("confirmation")
        prefecture_url(prefecture)
        now = self.clock()
        if now < self._next_request.get(prefecture, now):
            raise ExtractFailure("wait")
        try:
            previous = self.load(prefecture)
        except ExtractFailure as error:
            if error.reason != "cache":
                raise
            previous = None  # 破損キャッシュは再取得で修復するが、成功前には上書きしない。
        self._next_request[prefecture] = now + timedelta(minutes=10)
        checker = ExtractChecker(previous.state if previous else ExtractState())
        plan = checker.plan({"User-Agent": "PaperPhone/0.1.0", "Accept": "application/x-pbf"})
        try:
            with self.transport(prefecture_url(prefecture), plan.headers) as response:
                status = response.status
                if status == 304:
                    if previous is None or not plan.conditional:
                        raise ExtractFailure("invalid")
                    return ExtractUpdate(previous, False)
                if status == 429:
                    retry = {k.lower(): v for k, v in response.headers.items()}.get("retry-after", "")
                    try:
                        until = now + timedelta(seconds=max(0, int(retry)))
                    except (ValueError, OverflowError):
                        try:
                            until = parsedate_to_datetime(retry)
                            if until.tzinfo is None:
                                raise ValueError
                        except (TypeError, ValueError, OverflowError):
                            until = self._next_request[prefecture]
                    self._next_request[prefecture] = max(self._next_request[prefecture], until)
                if status != 200:
                    raise ExtractFailure({404: "missing", 403: "denied", 429: "limited"}.get(status, "http"))
                outcome, fresh = checker.observe(status, response.headers)
                if outcome != RELOADED:
                    raise ExtractFailure("invalid")
                basis = _basis(fresh.state, prefecture)
                return self._save(prefecture, response, fresh.state, basis)
        except ExtractFailure:
            raise
        except ExtractsError:
            raise ExtractFailure("invalid") from None
        except OSError as error:
            # 接続失敗とローカル権限不足を区別する。URLなどの詳細は公開しない。
            if isinstance(error, PermissionError):
                raise ExtractFailure("storage") from None
            raise ExtractFailure("network") from None
        except Exception:
            raise ExtractFailure("network") from None

    def _save(self, prefecture, response, state, basis):
        temporary = None
        meta_path = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            checksum, count, prefix = hashlib.sha256(), 0, b""
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix=".pbf-", delete=False) as sink:
                temporary = Path(sink.name)
                while chunk := response.read(1024 * 1024):
                    count += len(chunk)
                    if count > MAX_EXTRACT_BYTES:
                        raise ExtractFailure("invalid")
                    prefix += chunk[:max(0, 65540 - len(prefix))]
                    checksum.update(chunk)
                    sink.write(chunk)
                sink.flush()
                os.fsync(sink.fileno())
            if (not _pbf_header(prefix) or (state.bytes_count is not None and count != state.bytes_count)):
                raise ExtractFailure("invalid")
            digest = checksum.hexdigest()
            final = self.directory / f"JP-{prefecture:02d}-{digest}.osm.pbf"
            os.replace(temporary, final)
            temporary = None
            # Content-Lengthが無い転送でも、実際のバイト数を保存する。
            state = ExtractState(state.etag, state.last_modified, count, state.filename)
            metadata = {"prefecture": prefecture, "sha256": digest, "state": state.as_dict()}
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                              prefix=".meta-", delete=False) as sink:
                meta_path = Path(sink.name)
                json.dump(metadata, sink)
                sink.flush()
                os.fsync(sink.fileno())
            os.replace(meta_path, self._manifest(prefecture))
            meta_path = None
            return ExtractUpdate(CachedExtract(prefecture, final, digest, state, basis), True)
        except ExtractFailure:
            raise
        except OSError:
            raise ExtractFailure("storage") from None
        finally:
            # この呼出しでアプリ専用ディレクトリに作成した一時ファイルだけを掃除する。
            for path in (temporary, meta_path):
                if path is not None:
                    path.unlink(missing_ok=True)

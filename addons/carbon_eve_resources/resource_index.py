"""Pure-Python EVE file-index acquisition, browsing, and payload cache.

This module deliberately has no Blender dependency so its parser, cache, and
network behavior can be tested with the standard Python runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Callable, Iterable, Optional, Sequence
from urllib.request import Request, urlopen


METADATA_BASE_URL = "https://binaries.eveonline.com"
INDEX_BASE_URL = "https://binaries.eveonline.com"
APP_BASE_URL = "https://binaries.eveonline.com"
RESOURCE_BASE_URL = "https://resources.eveonline.com"
USER_AGENT = "CarbonEngineJS-tool-blender/0.2.2"
LATEST_BUILD_CHECK_INTERVAL_SECONDS = 12 * 60 * 60
DETAIL_VARIANT_MARKERS = ("_lowdetail", "_mediumdetail")
_MD5_RE = re.compile(r"^[0-9a-f]{32}$")
_BUILD_RE = re.compile(r"^[0-9]+$")


class ResourceIndexError(RuntimeError):
    """Raised when an index, cache entry, or remote resource is invalid."""


@dataclass(frozen=True, slots=True)
class IndexEntry:
    logical_path: str
    relative_path: str
    location: str
    checksum: Optional[str]
    uncompressed_size: Optional[int]
    compressed_size: Optional[int]
    binary_operation: Optional[int] = None

    @property
    def extension(self) -> str:
        return Path(self.relative_path).suffix.lower()


@dataclass(frozen=True, slots=True)
class BrowserEntry:
    logical_path: str
    name: str
    is_directory: bool
    resource: Optional[IndexEntry] = None


@dataclass(frozen=True, slots=True)
class FetchResult:
    entry: IndexEntry
    path: Path
    cache_hit: bool


@dataclass(frozen=True, slots=True)
class CacheStats:
    file_count: int
    byte_count: int


class ResourceCatalog:
    """One exact-build main resfileindex prepared for UI browsing."""

    def __init__(
        self,
        build: str,
        entries: Sequence[IndexEntry],
        cache_root: Path,
        cache_hit: bool,
        latest_check_deferred_seconds: int = 0,
    ):
        self.build = _normalize_build(build)
        self.entries = tuple(entries)
        self.cache_root = Path(cache_root).expanduser().resolve()
        self.cache_hit = bool(cache_hit)
        self.latest_check_deferred_seconds = max(0, int(latest_check_deferred_seconds))
        self._by_path = {entry.logical_path: entry for entry in self.entries}
        self.hidden_detail_count = sum(is_detail_variant(entry) for entry in self.entries)

    def get(self, logical_path: str) -> IndexEntry:
        normalized = normalize_logical_path(logical_path, "res")
        try:
            return self._by_path[normalized]
        except KeyError as exc:
            raise ResourceIndexError(f"Resource not found: {normalized}") from exc

    def browse(
        self,
        directory: str = "res:/",
        query: str = "",
        extensions: Optional[Iterable[str]] = None,
        show_lowdetail: bool = False,
        show_mediumdetail: bool = False,
        limit: int = 300,
    ) -> tuple[BrowserEntry, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")

        allowed = None
        if extensions is not None:
            allowed = {value.lower() if value.startswith(".") else f".{value.lower()}" for value in extensions}

        query_text = query.strip().lower()
        if query_text:
            matches = []
            for entry in self.entries:
                if _hide_detail_variant(entry, show_lowdetail, show_mediumdetail):
                    continue
                if query_text not in entry.logical_path:
                    continue
                if allowed is not None and entry.extension not in allowed:
                    continue
                matches.append(BrowserEntry(entry.logical_path, Path(entry.relative_path).name, False, entry))
                if len(matches) >= limit:
                    break
            return tuple(matches)

        normalized_directory = normalize_directory(directory)
        relative_prefix = normalized_directory[5:]
        folders: dict[str, BrowserEntry] = {}
        files: list[BrowserEntry] = []

        for entry in self.entries:
            if _hide_detail_variant(entry, show_lowdetail, show_mediumdetail):
                continue
            if not entry.relative_path.startswith(relative_prefix):
                continue
            remainder = entry.relative_path[len(relative_prefix):]
            if not remainder:
                continue
            head, separator, _tail = remainder.partition("/")
            if separator:
                path = f"{normalized_directory}{head}/"
                folders.setdefault(path, BrowserEntry(path, head, True))
                continue
            if allowed is not None and entry.extension not in allowed:
                continue
            files.append(BrowserEntry(entry.logical_path, head, False, entry))

        results = sorted(folders.values(), key=lambda item: item.name.lower())
        results.extend(sorted(files, key=lambda item: item.name.lower()))
        return tuple(results[:limit])


def is_detail_variant(entry: IndexEntry) -> bool:
    """Identifies authored low/medium-detail file variants hidden by the browser."""

    return detail_variant_kind(entry) is not None


def detail_variant_kind(entry: IndexEntry) -> Optional[str]:
    """Returns LOW, MEDIUM, or None for one indexed filename."""

    file_name = entry.relative_path.rsplit("/", 1)[-1]
    if DETAIL_VARIANT_MARKERS[0] in file_name:
        return "LOW"
    if DETAIL_VARIANT_MARKERS[1] in file_name:
        return "MEDIUM"
    return None


def _hide_detail_variant(entry: IndexEntry, show_lowdetail: bool, show_mediumdetail: bool) -> bool:
    kind = detail_variant_kind(entry)
    return (kind == "LOW" and not show_lowdetail) or (kind == "MEDIUM" and not show_mediumdetail)


def normalize_logical_path(value: str, default_root: str = "res") -> str:
    text = str(value or "").strip().replace("\\", "/").lower()
    if not text or "\0" in text:
        raise ResourceIndexError("Logical path is required")
    if ":/" not in text:
        text = f"{default_root}:/{text.lstrip('/')}"
    root, relative = text.split(":/", 1)
    if not re.fullmatch(r"[a-z][a-z0-9+.-]*", root):
        raise ResourceIndexError(f"Invalid logical root: {root}")
    segments = _safe_segments(relative, "logical path")
    if not segments:
        raise ResourceIndexError(f"Invalid logical path: {value}")
    return f"{root}:/{'/'.join(segments)}"


def normalize_directory(value: str) -> str:
    text = str(value or "res:/").strip().replace("\\", "/").lower()
    if text in {"res:", "res:/", "/", ""}:
        return "res:/"
    normalized = normalize_logical_path(text.rstrip("/"), "res")
    if not normalized.startswith("res:/"):
        raise ResourceIndexError("Only res:/ directories can be browsed")
    return f"{normalized}/"


def normalize_storage_path(value: str) -> str:
    segments = _safe_segments(str(value or "").strip().replace("\\", "/"), "storage path")
    if len(segments) < 2 or not re.fullmatch(r"[0-9a-fA-F]{2}", segments[0]):
        raise ResourceIndexError(f"Invalid indexed storage path: {value}")
    if any(":" in segment for segment in segments):
        raise ResourceIndexError(f"Invalid indexed storage path: {value}")
    return "/".join(segments)


def parse_index_line(line: str, line_number: int = 1, default_root: str = "res") -> IndexEntry:
    columns = [value.strip() for value in line.split(",")]
    if len(columns) < 2 or len(columns) > 6:
        raise ResourceIndexError(f"Invalid index row at line {line_number}: expected 2 to 6 columns")
    logical, location = columns[0:2]
    if not logical or not location:
        raise ResourceIndexError(f"Invalid index row at line {line_number}: missing path or location")
    normalized = normalize_logical_path(logical, default_root)
    root, relative = normalized.split(":/", 1)
    if root not in {"app", "res"}:
        raise ResourceIndexError(f"Unsupported index root at line {line_number}: {root}")

    checksum = _optional_md5(columns[2] if len(columns) > 2 else None, line_number)
    return IndexEntry(
        logical_path=normalized,
        relative_path=relative,
        location=normalize_storage_path(location),
        checksum=checksum,
        uncompressed_size=_optional_integer(columns[3] if len(columns) > 3 else None, "size", line_number),
        compressed_size=_optional_integer(columns[4] if len(columns) > 4 else None, "compressed size", line_number),
        binary_operation=_optional_integer(columns[5] if len(columns) > 5 else None, "binary operation", line_number),
    )


def parse_index(text: str, default_root: str = "res") -> tuple[IndexEntry, ...]:
    entries = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if line:
            entries.append(parse_index_line(line, line_number, default_root))
    return tuple(entries)


def default_cache_root() -> Path:
    override = os.environ.get("CARBONENGINEJS_TOOL_CACHE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "carbonenginejs" / "tool-core"


def open_cached_catalog(cache_root: Path) -> Optional[ResourceCatalog]:
    root = Path(cache_root).expanduser().resolve()
    builds_root = root / "ccp" / "builds"
    if not builds_root.is_dir():
        return None
    builds = sorted(
        (item.name for item in builds_root.iterdir() if item.is_dir() and _BUILD_RE.fullmatch(item.name)),
        key=int,
        reverse=True,
    )
    for build in builds:
        indexes = builds_root / build / "indexes"
        app_path = indexes / "appfileindex.txt"
        res_path = indexes / "resfileindex.txt"
        if not app_path.is_file() or not res_path.is_file():
            continue
        try:
            app_entries = parse_index(app_path.read_text(encoding="utf-8"), "app")
            declaration = _find_main_index(app_entries)
            res_bytes = res_path.read_bytes()
            validate_bytes(res_bytes, declaration, "cached resfileindex")
            return ResourceCatalog(build, parse_index(res_bytes.decode("utf-8"), "res"), root, True)
        except (OSError, UnicodeError, ResourceIndexError):
            continue
    return None


def ensure_latest_catalog(
    cache_root: Path,
    *,
    creator_terms_accepted: bool = False,
    channel: str = "TQ",
    offline_first: bool = True,
    min_check_interval: float = LATEST_BUILD_CHECK_INTERVAL_SECONDS,
    clock: Callable[[], float] = time.time,
    opener: Callable = urlopen,
    timeout: float = 30.0,
) -> ResourceCatalog:
    _require_creator_terms(creator_terms_accepted)
    root = Path(cache_root).expanduser().resolve()
    token = str(channel or "TQ").strip().upper()
    if not re.fullmatch(r"[A-Z0-9_-]+", token):
        raise ResourceIndexError(f"Invalid EVE channel: {channel}")
    try:
        interval = float(min_check_interval)
        checked_at = float(clock())
    except (TypeError, ValueError, OverflowError) as exc:
        raise ResourceIndexError("Invalid latest-build check interval or clock") from exc
    if not math.isfinite(interval) or interval < 0 or not math.isfinite(checked_at):
        raise ResourceIndexError("Invalid latest-build check interval or clock")

    cached = open_cached_catalog(root)
    if offline_first:
        if cached is not None:
            return cached

    recent = _read_latest_build_check(root, token, checked_at, interval)
    deferred_seconds = 0
    if recent is not None:
        build, deferred_seconds = recent
        if cached is not None and cached.build == build:
            cached.latest_check_deferred_seconds = deferred_seconds
            return cached
    else:
        metadata_url = f"{METADATA_BASE_URL}/eveclient_{token}.json"
        metadata = json.loads(_download(metadata_url, opener, timeout).decode("utf-8"))
        build = _normalize_build(metadata.get("build", metadata.get("buildNumber", "")))
        _write_latest_build_check(root, token, build, checked_at)

    index_dir = root / "ccp" / "builds" / build / "indexes"
    app_path = index_dir / "appfileindex.txt"
    res_path = index_dir / "resfileindex.txt"

    app_bytes = _read_valid_file(app_path, None)
    if app_bytes is None:
        app_url = f"{INDEX_BASE_URL}/eveonline_{build}.txt"
        app_bytes = _download(app_url, opener, timeout)
        parse_index(app_bytes.decode("utf-8"), "app")
        _write_atomic(app_path, app_bytes)

    app_entries = parse_index(app_bytes.decode("utf-8"), "app")
    declaration = _find_main_index(app_entries)
    res_bytes = _read_valid_file(res_path, declaration)
    cache_hit = res_bytes is not None
    if res_bytes is None:
        res_url = f"{APP_BASE_URL}/{declaration.location}"
        res_bytes = _download(res_url, opener, timeout)
        validate_bytes(res_bytes, declaration, "resfileindex")
        parse_index(res_bytes.decode("utf-8"), "res")
        _write_atomic(res_path, res_bytes)

    return ResourceCatalog(
        build,
        parse_index(res_bytes.decode("utf-8"), "res"),
        root,
        cache_hit,
        latest_check_deferred_seconds=deferred_seconds,
    )


def fetch_resource(
    entry: IndexEntry,
    cache_root: Path,
    *,
    creator_terms_accepted: bool = False,
    refresh: bool = False,
    opener: Callable = urlopen,
    timeout: float = 60.0,
) -> FetchResult:
    _require_creator_terms(creator_terms_accepted)
    root = Path(cache_root).expanduser().resolve()
    path = safe_join(root, "ResFiles", *entry.location.split("/"))
    if not refresh:
        cached = _read_valid_file(path, entry)
        if cached is not None:
            return FetchResult(entry, path, True)
    url = f"{RESOURCE_BASE_URL}/{entry.location}"
    payload = _download(url, opener, timeout)
    validate_bytes(payload, entry, entry.logical_path)
    _write_atomic(path, payload)
    return FetchResult(entry, path, False)


def materialize_resource(
    entry: IndexEntry,
    cache_root: Path,
    output_root: Path,
    *,
    creator_terms_accepted: bool = False,
    opener: Callable = urlopen,
    timeout: float = 60.0,
) -> FetchResult:
    _require_creator_terms(creator_terms_accepted)
    fetched = fetch_resource(
        entry,
        cache_root,
        creator_terms_accepted=True,
        opener=opener,
        timeout=timeout,
    )
    destination = safe_join(Path(output_root).expanduser().resolve(), *entry.relative_path.split("/"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        shutil.copyfile(fetched.path, temporary)
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return FetchResult(entry, destination, fetched.cache_hit)


def payload_cache_stats(cache_root: Path) -> CacheStats:
    """Counts unique downloaded payloads, excluding indexes and preview copies."""

    payload_root = safe_join(Path(cache_root).expanduser().resolve(), "ResFiles")
    if not payload_root.is_dir():
        return CacheStats(0, 0)

    file_count = 0
    byte_count = 0
    pending = [payload_root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for item in entries:
                    if item.is_dir(follow_symlinks=False):
                        pending.append(Path(item.path))
                    elif item.is_file(follow_symlinks=False):
                        file_count += 1
                        byte_count += item.stat(follow_symlinks=False).st_size
        except FileNotFoundError:
            continue
    return CacheStats(file_count, byte_count)


def clear_payload_cache(cache_root: Path) -> CacheStats:
    """Clears downloaded payloads and derived previews, retaining indexes and exports."""

    root = Path(cache_root).expanduser().resolve()
    before = payload_cache_stats(root)
    for name in ("ResFiles", "Previews"):
        directory = safe_join(root, name)
        if directory.is_dir():
            shutil.rmtree(directory)
        elif directory.exists():
            directory.unlink()
    return before


def validate_bytes(data: bytes, expected: Optional[IndexEntry], label: str = "resource") -> bytes:
    if expected is None:
        return data
    if expected.uncompressed_size is not None and len(data) != expected.uncompressed_size:
        raise ResourceIndexError(
            f"{label} size mismatch: expected {expected.uncompressed_size}, received {len(data)}"
        )
    if expected.checksum is not None:
        actual = hashlib.md5(data).hexdigest()
        if actual != expected.checksum:
            raise ResourceIndexError(f"{label} MD5 mismatch: expected {expected.checksum}, received {actual}")
    return data


def _require_creator_terms(accepted: bool) -> None:
    if not accepted:
        raise ResourceIndexError("Accept the EVE Online Content Creation Terms of Use before using this tool")


def safe_join(root: Path, *segments: str) -> Path:
    root = Path(root).expanduser().resolve()
    destination = root.joinpath(*segments).resolve()
    try:
        common = Path(os.path.commonpath((str(root), str(destination))))
    except ValueError as exc:
        raise ResourceIndexError("Cache path escaped its root") from exc
    if common != root or destination == root:
        raise ResourceIndexError("Cache path escaped its root")
    return destination


def _latest_build_check_path(cache_root: Path, channel: str) -> Path:
    return safe_join(cache_root, "ccp", "channels", channel.lower(), "latest-build-check.json")


def _read_latest_build_check(
    cache_root: Path,
    channel: str,
    current_time: float,
    interval: float,
) -> Optional[tuple[str, int]]:
    if interval <= 0:
        return None
    try:
        data = json.loads(_latest_build_check_path(cache_root, channel).read_text(encoding="utf-8"))
        if data.get("schema") != 1 or data.get("channel") != channel:
            return None
        build = _normalize_build(data.get("build"))
        previous = float(data.get("checkedAt"))
        if not math.isfinite(previous) or previous < 0:
            return None
    except (AttributeError, OSError, TypeError, ValueError, ResourceIndexError):
        return None

    elapsed = max(0.0, current_time - previous)
    remaining = interval - elapsed
    if remaining <= 0:
        return None
    return build, max(1, int(math.ceil(remaining)))


def _write_latest_build_check(
    cache_root: Path,
    channel: str,
    build: str,
    checked_at: float,
) -> None:
    payload = json.dumps(
        {
            "schema": 1,
            "channel": channel,
            "build": _normalize_build(build),
            "checkedAt": checked_at,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    _write_atomic(_latest_build_check_path(cache_root, channel), payload)


def _download(url: str, opener: Callable, timeout: float) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        response = opener(request, timeout=timeout)
        with response:
            status = getattr(response, "status", 200)
            if status is not None and not 200 <= int(status) < 300:
                raise ResourceIndexError(f"Failed to download {url}: HTTP {status}")
            return response.read()
    except ResourceIndexError:
        raise
    except Exception as exc:
        raise ResourceIndexError(f"Failed to download {url}: {exc}") from exc


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_valid_file(path: Path, expected: Optional[IndexEntry]) -> Optional[bytes]:
    try:
        data = path.read_bytes()
        return validate_bytes(data, expected, str(path))
    except (OSError, ResourceIndexError):
        return None


def _find_main_index(entries: Sequence[IndexEntry]) -> IndexEntry:
    for entry in entries:
        if entry.logical_path == "app:/resfileindex.txt":
            return entry
    raise ResourceIndexError("appfileindex does not declare app:/resfileindex.txt")


def _safe_segments(value: str, label: str) -> list[str]:
    segments = [segment for segment in value.split("/") if segment]
    if any(segment in {".", ".."} or "\0" in segment for segment in segments):
        raise ResourceIndexError(f"Unsafe {label}")
    return segments


def _optional_md5(value: Optional[str], line_number: int) -> Optional[str]:
    if value is None or value == "":
        return None
    checksum = value.lower()
    if not _MD5_RE.fullmatch(checksum):
        raise ResourceIndexError(f"Invalid checksum at line {line_number}")
    return checksum


def _optional_integer(value: Optional[str], label: str, line_number: int) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        result = int(value)
    except ValueError as exc:
        raise ResourceIndexError(f"Invalid {label} at line {line_number}: {value}") from exc
    if result < 0:
        raise ResourceIndexError(f"Invalid {label} at line {line_number}: {value}")
    return result


def _normalize_build(value: object) -> str:
    build = str(value or "").strip()
    if not _BUILD_RE.fullmatch(build):
        raise ResourceIndexError(f"Invalid exact build: {value}")
    return build

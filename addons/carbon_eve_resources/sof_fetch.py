"""Fetches a ship straight from the service and CCP. No bundle, no Node.

    document   GET  /<target>/<build>/sof/dna/<dna>
    resource   POST /v1/resources/resolve   -> resolution.sourceUrl
    bytes      GET  sourceUrl               (resources.eveonline.com)

Files land in the shared cache under their content hash, which is what the
cache is keyed by anyway, so a texture two ships share is stored once.

Textures are used as downloaded: Blender reads EVE's DXT5 `.dds` natively.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
from typing import Callable, Mapping, Optional
from urllib.request import Request, urlopen

from .tools_remote import USER_AGENT


#: How many files to fetch at once. Bounded: the work is somebody else's
#: server, and a ship is only fifty files.
WORKERS = 8

#: The document names its resources with these keys.
RESOURCE_KEYS = ("geometryResPath", "resourcePath", "resFilePath", "textureResFilePath")


class FetchError(RuntimeError):
    """Raised when a ship cannot be fetched."""


def document_for(dna: str, client, *, build: str = "latest",
                 target: str = "eve") -> dict:
    """The built document for one DNA."""

    wanted = str(dna or "").strip().lower()
    if not wanted:
        raise FetchError("a DNA is required")
    try:
        found = client.request_json("GET", f"/{target}/{build}/sof/dna/{wanted}")
    except Exception as exc:
        raise FetchError(f"{wanted}: {exc}") from exc
    if not isinstance(found, Mapping):
        raise FetchError(f"{wanted}: the service did not answer with a document")
    return dict(found)


def resource_paths(document) -> tuple:
    """Every `res:/` path the document mentions, in document order.

    Walked rather than read from a manifest: without a bundle there is no
    manifest, and the document is the only statement of what a ship needs.
    """

    found = []
    seen = set()

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, Mapping):
            return
        for key, value in node.items():
            if key in RESOURCE_KEYS and isinstance(value, str):
                path = value.strip()
                if path.lower().startswith("res:/") and path not in seen:
                    seen.add(path)
                    found.append(path)
            else:
                walk(value)

    walk(document)
    return tuple(found)


def cache_file(cache_root, source_url: str) -> Path:
    """Where one resource is cached, by the hash CCP gives it.

    The same layout the tools-core cache uses -- `ResFiles/<2 hex>/<name>` --
    so a cache filled by either side serves both.
    """

    name = str(source_url or "").rstrip("/").rsplit("/", 1)[-1]
    if not name:
        raise FetchError(f"cannot name a cache file for {source_url}")
    return Path(cache_root) / "ResFiles" / name[:2] / name


def download(source_url: str, destination: Path, *, opener=urlopen,
             timeout: float = 120.0) -> Path:
    """Fetches one file, unless it is already cached.

    Written to a temporary name and moved, so an interrupted download cannot
    leave a half file that looks cached.
    """

    if destination.is_file() and destination.stat().st_size > 0:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(source_url, headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=timeout) as response:
            payload = response.read()
    except Exception as exc:
        raise FetchError(f"{source_url}: {exc}") from exc
    partial = destination.with_suffix(destination.suffix + ".part")
    partial.write_bytes(payload)
    partial.replace(destination)
    return destination


def shared_index(shared_cache) -> dict:
    """`res:/path -> hash` from an EVE install's own index, if it has one.

    An installed client keeps `resfileindex.txt`, whose lines are
    `logical path,storage path,md5,size,...`. With it a file needs no `resolve`
    call at all, which is the slow part of a fetch.

    Empty when there is no install or no index: the caller then resolves as
    usual, which is slower but always works.
    """

    root = Path(shared_cache) if shared_cache else None
    if root is None or not root.is_dir():
        return {}
    for name in ("resfileindex.txt", "tq/resfileindex.txt", "index_tq.txt"):
        found = root / name
        if not found.is_file():
            continue
        index = {}
        try:
            for line in found.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.split(",")
                if len(parts) >= 2 and parts[0].lower().startswith("res:/"):
                    index[parts[0].strip().lower()] = parts[1].strip()
        except OSError:
            return {}
        return index
    return {}


def shared_file(index, logical_path: str, shared_cache):
    """The file an EVE install already has for one resource, or None."""

    if not index or not shared_cache:
        return None
    storage = index.get(str(logical_path).strip().lower())
    if not storage:
        return None
    found = Path(shared_cache) / "ResFiles" / storage.replace("\\", "/")
    return found if found.is_file() else None


def fetch_ship(dna: str, client, cache_root, *, build: str = "",
               target: str = "eve", progress: Optional[Callable] = None,
               opener=urlopen, cancelled: Optional[Callable] = None,
               shared_cache=None) -> tuple:
    """`(document, {res path: local file})` for one DNA.

    A resource that cannot be fetched is left OUT of the map rather than
    failing the ship: the builder already reports what it could not find, and
    one missing texture should not cost a whole hull.
    """

    exact = str(build or "").strip()
    if not exact or exact == "latest":
        answer = client.request_json("GET", f"/{target}/latest/build")
        exact = str((answer or {}).get("build") or "").strip()
        if not exact:
            raise FetchError("the service did not report a build")

    document = document_for(dna, client, build=exact, target=target)
    paths = resource_paths(document)
    resources = {}
    problems = []

    index = shared_index(shared_cache) if shared_cache else {}
    if index and progress is not None:
        progress(f"{len(index)} files in the EVE cache")

    def one(path):
        # The local EVE install first, when there is one: the file is already
        # on disk under the same content hash, so there is nothing to resolve
        # and nothing to download.
        local = shared_file(index, path, shared_cache)
        if local is not None:
            return str(local)
        found = client.resolve_resource(path, exact, target=target)
        url = str(((found or {}).get("resolution") or found or {}).get("sourceUrl") or "")
        if not url:
            raise FetchError("no source url")
        return str(download(url, cache_file(cache_root, url), opener=opener))

    # In parallel. A ship is fifty files, each a resolve and a download, and
    # serially that is over two minutes of a person watching nothing happen.
    # Bounded because the work is somebody else's server.
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        running = {pool.submit(one, path): path for path in paths}
        for finished in as_completed(running):
            path = running[finished]
            done += 1
            if progress is not None:
                # Named for what it is doing. "12/50 gb2_t1_a.dds" reads as
                # downloading, and the download is the fast part -- what takes
                # the time is asking the service where each file lives.
                progress(f"Resolving {done}/{len(paths)} {path.rsplit('/', 1)[-1]}")
            if cancelled is not None and cancelled():
                for pending in running:
                    pending.cancel()
                problems.append("cancelled")
                break
            try:
                resources[path] = finished.result()
            except Exception as exc:
                problems.append(f"{path}: {exc}")
    return document, resources, problems


def write_document(document, destination: Path) -> Path:
    """Writes the document beside the cache, for the builder to read.

    `build_ship` takes a path rather than an object, and one temporary file is
    a smaller change than reworking its signature.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(document), encoding="utf-8")
    return destination

"""Fetches a ship straight from the service and CCP. No bundle, no Node.

    document   GET  /<target>/<build>/sof/dna/<dna>
    where      POST /v1/resources/resolve  -> resolution.sourceUrl
    bytes      GET  sourceUrl              (resources.eveonline.com)

The BYTES come from CCP, not from the service. Files are stored
content-addressed under `ResFiles/<shard>/<pathHash>_<md5>`, which is how EVE
stores them and how tools-core caches them, so a file already on disk is found
whoever put it there.

Textures are used as downloaded: Blender reads EVE's DXT5 `.dds` natively.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
from typing import Callable, Mapping, Optional
from urllib.request import Request, urlopen

from . import resfile, resindex
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


def cache_file(cache_root, logical_path: str, md5: str) -> Path:
    """Where one resource is stored: `ResFiles/<shard>/<pathHash>_<md5>`.

    Content-addressed, exactly as EVE stores it and as tools-core caches it:
    the shard is the first two characters of the FNV-1 hash of the logical
    path, and the file has no extension. Keying by anything else orphans every
    file already on disk.
    """

    return resfile.cache_path(cache_root, logical_path, md5)


#: What a texture may be provided as, in the order they are preferred.
#: Hand-authored first (TGA, then PNG), the shipped DDS last -- so dropping a
#: file in beside the original overrides it without renaming anything.
TEXTURE_SUFFIXES = (".tga", ".png", ".dds")


def local_file(local_root, logical_path: str):
    """A locally provided file for one resource, or None.

    The optional directory mirrors the resfileindex's LOGICAL paths -- so
    `res:/dx9/model/ship/.../gb2_t1_a.dds` is `dx9/model/ship/.../gb2_t1_a.tga`
    under the root -- which is how a person can drop in their own textures
    without knowing anything about content addressing.

    A texture is looked for as TGA, then PNG, then DDS; anything else is looked
    for under its own name. Nothing here writes: this is somebody's source
    folder, not a cache.
    """

    if not local_root:
        return None
    relative = str(logical_path or "").split(":/", 1)[-1].strip("/")
    if not relative:
        return None
    found = Path(local_root) / relative
    if found.suffix.lower() == ".dds":
        for suffix in TEXTURE_SUFFIXES:
            candidate = found.with_suffix(suffix)
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        return None
    return found if found.is_file() and found.stat().st_size > 0 else None


def fetch_resource(logical_path: str, client, cache_root, *, build: str,
                   target: str = "eve", opener=urlopen, index=None,
                   local_root=None) -> Path:
    """One resource's bytes: cached if present, else fetched from CCP.

    The INDEX answers where a file lives, so nothing is asked per file. Falling
    back to the service's resolve only when the index has no row keeps a ship
    loadable when the index is old or a path is overlay-only.
    """

    # In order:
    #   1. the optional folder, .tga then .dds -- what the person is authoring
    #   2. the cache under the same human-readable layout, .tga then .dds
    #   3. the cache's content-addressed store, where downloads land
    #   4. the index, and CCP
    provided = local_file(local_root, logical_path)
    if provided is not None:
        return provided

    provided = local_file(cache_root, logical_path)
    if provided is not None:
        return provided

    cached = resfile.find_cached(cache_root, logical_path)
    if cached is not None:
        return cached

    location = resindex.locate(index, logical_path) if index else None
    if location:
        url = resindex.source_url(location)
    else:
        found = client.resolve_resource(logical_path, build, target=target)
        resolution = (found or {}).get("resolution") or found or {}
        url = str(resolution.get("sourceUrl") or "")
        location = "/".join(url.split("/")[-2:]) if url else ""
    if not url:
        raise FetchError(f"{logical_path}: no source for it")

    payload = read_url(url, opener=opener)
    stored = resfile.parse(location)
    if stored is None:
        destination = cache_file(cache_root, logical_path, resfile.md5_of(payload))
    else:
        destination = (Path(cache_root) / "ResFiles" / stored["shard"]
                       / f"{stored['path_hash']}_{stored['checksum']}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    partial.write_bytes(payload)
    partial.replace(destination)
    return destination


def read_url(url: str, *, opener=urlopen, timeout: float = 120.0) -> bytes:
    """The bytes at one URL."""

    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=timeout) as response:
            return response.read()
    except Exception as exc:
        raise FetchError(f"{url}: {exc}") from exc


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


def fetch_ship(dna: str, client, cache_root, *, build: str = "",
               target: str = "eve", progress: Optional[Callable] = None,
               opener=urlopen, cancelled: Optional[Callable] = None,
               local_root=None) -> tuple:
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

    # One index per build instead of a question per file.
    try:
        index = resindex.load(cache_root, exact)
    except Exception as exc:
        print(f"[CarbonEngineJS SOF] index unavailable, resolving per file: {exc}")
        index = None

    document = document_for(dna, client, build=exact, target=target)
    paths = resource_paths(document)
    resources = {}
    problems = []

    def one(path):
        return str(fetch_resource(path, client, cache_root, build=exact,
                                  target=target, opener=opener, index=index,
                                  local_root=local_root))

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
                progress(f"{done}/{len(paths)} {path.rsplit('/', 1)[-1]}")
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

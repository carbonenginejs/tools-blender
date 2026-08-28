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
import gzip
import hashlib
import json
import os
import re
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


#: Windows' extended-length prefix. Written as a raw string because it is four
#: characters -- backslash, backslash, question mark, backslash -- and every
#: other spelling of it collapses to something that silently does nothing.
LONG_PREFIX = r"\\?" + "\\"


def long_path(path) -> str:
    """A path Windows will accept however long it is.

    `MAX_PATH` is 260 and a skinned DNA reaches it: the document for
    `ab3_t1:amarrbase:amarr:mesh?...:respathinsert?amarr` lands at 261
    characters under the default cache root, and the write fails.

    The extended-length prefix lifts that to 32767. It applies to absolute
    Windows paths only, needs backslashes throughout, and is a no-op
    everywhere else -- which is why the name stays the DNA rather than becoming
    a generated id nobody can read.
    """

    text = str(Path(path).resolve())
    if os.name == "nt" and not text.startswith(LONG_PREFIX):
        return LONG_PREFIX + text.replace("/", "\\")
    return text


def document_path(cache_root, dna: str, build: str, target: str = "eve") -> Path:
    """Where one DNA's document is kept.

    Under the BUILD, so a new client build fetches a fresh document rather
    than serving the old one forever. Gzipped: a document is 259KB of JSON and
    39KB compressed, and a person may accumulate hundreds.

    Named for the DNA itself, with the punctuation a filesystem will not take
    replaced. No generated id: the DNA is already unique and already readable,
    and a file called `ab3_t1_amarrbase_amarr_mesh_blue_darknavy_enamel...` can
    be found by eye, which one called `cb04a41f...` cannot.
    """

    safe = re.sub(r"[^a-z0-9_.-]+", "_", str(dna or "").strip().lower()).strip("_")
    return Path(cache_root) / "documents" / str(build) / target / f"{safe}.json.gz"


#: What a stored document is wrapped in. The envelope is the point: the digest
#: says WHICH document this is, so a consumer can tell a ship that genuinely
#: changed from one whose build number merely moved, and can prove a file is
#: intact without re-fetching it.
DOCUMENT_ENVELOPE = "carbon.blender.document"
ENVELOPE_VERSION = 1


def document_digest(document) -> str:
    """A stable sha256 over one document.

    Keys sorted and separators fixed, so the same document hashes the same on
    any machine and in any Python. This is the value worth comparing: two
    builds carrying the same digest for a DNA mean nothing about that ship
    changed, whatever the build numbers say.
    """

    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_document(path, *, verify: bool = True) -> dict:
    """A stored document, checked against its own digest."""

    with gzip.open(long_path(path), "rt", encoding="utf-8") as handle:
        envelope = json.load(handle)
    if not isinstance(envelope, Mapping) or envelope.get("envelope") != DOCUMENT_ENVELOPE:
        raise FetchError(f"{path}: not a stored document")
    document = envelope.get("document")
    if not isinstance(document, Mapping):
        raise FetchError(f"{path}: the envelope carries no document")
    if verify and envelope.get("sha256") != document_digest(document):
        raise FetchError(f"{path}: digest does not match its contents")
    return dict(document)


def read_envelope(path) -> dict:
    """The envelope WITHOUT the document: dna, build, digest, when.

    Reading this is cheap and answers "has this ship changed?" without loading
    259KB of JSON to find out.
    """

    with gzip.open(long_path(path), "rt", encoding="utf-8") as handle:
        envelope = json.load(handle)
    if not isinstance(envelope, Mapping):
        raise FetchError(f"{path}: not a stored document")
    return {key: value for key, value in envelope.items() if key != "document"}


def write_document_cache(path, document, *, dna: str = "", build: str = "",
                         target: str = "eve") -> str:
    """Stores one document with its digest. Returns the digest.

    Written to a temporary name and moved, so a kill cannot leave half a file
    that reads as cached.
    """

    path = Path(path)
    os.makedirs(long_path(path.parent), exist_ok=True)
    digest = document_digest(document)
    envelope = {
        "envelope": DOCUMENT_ENVELOPE,
        "version": ENVELOPE_VERSION,
        "dna": str(dna),
        "build": str(build),
        "target": str(target),
        "sha256": digest,
        "document": document,
    }
    partial = path.with_name(path.name + ".part")
    with gzip.open(long_path(partial), "wt", encoding="utf-8", compresslevel=9) as handle:
        json.dump(envelope, handle, separators=(",", ":"))
    os.replace(long_path(partial), long_path(path))
    return digest


def document_for(dna: str, client, *, build: str = "latest",
                 target: str = "eve", cache_root=None) -> dict:
    """The built document for one DNA, from disk when it is there.

    Stored so a ship already loaded once can be loaded again with the service
    down. The service is asked only when the document is not on disk, and a
    failure then still checks disk before giving up -- being offline should
    cost the ships you have never opened, not the ones you have.
    """

    wanted = str(dna or "").strip().lower()
    if not wanted:
        raise FetchError("a DNA is required")

    cached = document_path(cache_root, wanted, build, target) if cache_root else None
    # `is_file` cannot see a path over MAX_PATH either, so the check goes
    # through the same prefix the read does.
    if cached is not None and os.path.isfile(long_path(cached)):
        try:
            return read_document(cached)
        except (OSError, ValueError, FetchError):
            pass                       # a bad file is not worth keeping

    try:
        found = client.request_json("GET", f"/{target}/{build}/sof/dna/{wanted}")
    except Exception as exc:
        raise FetchError(f"{wanted}: {exc}") from exc
    if not isinstance(found, Mapping):
        raise FetchError(f"{wanted}: the service did not answer with a document")

    document = dict(found)
    if cached is not None:
        try:
            write_document_cache(cached, document, dna=wanted, build=build,
                                 target=target)
        except OSError as exc:
            print(f"[CarbonEngineJS SOF] could not store {cached.name}: {exc}")
    return document


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


def local_at_address(root, location: str):
    """A locally provided file sitting at the resource's cache address.

    The local folder mirrors the cache exactly: same shard, same name, no
    extension -- so dropping a file in overrides that resource and nothing has
    to be renamed or looked up. A file the person has TRANSLATED keeps the
    extension it was translated to, the same way ours do, so both
    `<address>` and `<address>.png` are theirs to provide.
    """

    if not root or not location:
        return None
    base = resfile.stored_path(root, location)
    if base is None:
        return None
    for candidate in (base,) + tuple(base.with_suffix(s)
                                     for s in TEXTURE_SUFFIXES):
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def fetch_resource(logical_path: str, client, cache_root, *, build: str,
                   target: str = "eve", opener=urlopen, index=None,
                   local_root=None, resfiles_root=None, sources=None) -> Path:
    """One resource's bytes: cached if present, else fetched from CCP.

    The INDEX answers where a file lives, so nothing is asked per file. Falling
    back to the service's resolve only when the index has no row keeps a ship
    loadable when the index is old or a path is overlay-only.
    """

    # In order:
    #   1. local ResFiles, laid out the way the cache is
    #   2. the authored folder, by logical path, .tga then .png then .dds
    #   3. the cache under that same human-readable layout
    #   4. the cache's own store, where downloads land
    #   5. the index, and CCP
    #
    # Both local folders are READ ONLY. Nothing is ever written into either --
    # a texture translated out of one goes to our cache, beside where the
    # source WOULD live, not beside the file it came from.
    def note(kind, path):
        # Where each file CAME from, so a load can say so. Every load reads the
        # same fifty names aloud, and a person watching that cannot tell a
        # cached ship from one being downloaded unless it says which.
        if sources is not None:
            sources[logical_path] = kind
        return path

    # The index gives the address; nothing is computed from the path. Asking
    # the service to resolve is the fallback for a path it has no row for.
    row = resindex.locate(index, logical_path) if index else None

    provided = local_at_address(resfiles_root, row)
    if provided is None:
        provided = local_file(local_root, logical_path)
    if provided is not None:
        return note("local", provided)

    provided = local_file(cache_root, logical_path)
    if provided is not None:
        return note("cache", provided)

    def stored(location):
        found = resfile.stored_path(cache_root, location, logical_path)
        if found is not None and found.is_file() and found.stat().st_size > 0:
            return found
        return None

    location = row
    url = resindex.source_url(location) if location else ""
    if location:
        cached = stored(location)
        if cached is not None:
            return note("cache", cached)

    if not url:
        found = client.resolve_resource(logical_path, build, target=target)
        resolution = (found or {}).get("resolution") or found or {}
        url = str(resolution.get("sourceUrl") or "")
        location = "/".join(url.split("/")[-2:]) if url else ""
        cached = stored(location) if location else None
        if cached is not None:
            return note("cache", cached)
    if not url:
        raise FetchError(f"{logical_path}: no source for it")

    payload = read_url(url, opener=opener)
    destination = resfile.stored_path(cache_root, location, logical_path)
    if destination is None:
        # No usable address: the human-readable layout, which is the same
        # place the optional local-files folder is read from.
        destination = resfile.readable_path(cache_root, logical_path)
        if destination is None:
            raise FetchError(f"{logical_path}: nowhere to store it")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    partial.write_bytes(payload)
    partial.replace(destination)
    return note("download", destination)


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
               local_root=None, resfiles_root=None) -> tuple:
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

    document = document_for(dna, client, build=exact, target=target,
                            cache_root=cache_root)
    paths = resource_paths(document)
    resources = {}
    problems = []

    sources = {}

    def one(path):
        return str(fetch_resource(path, client, cache_root, build=exact,
                                  target=target, opener=opener, index=index,
                                  local_root=local_root,
                                  resfiles_root=resfiles_root,
                                  sources=sources))

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
                # Say which, not just which file. "12/50 gb2_t1_a.dds" reads as
                # downloading whatever it did, so a ship already on disk looked
                # identical to one arriving over the wire.
                verb = {"local": "reading", "cache": "cached",
                        "download": "downloading"}.get(sources.get(path), "")
                progress(f"{done}/{len(paths)} {verb} "
                         f"{path.rsplit('/', 1)[-1]}".replace("  ", " "))
            if cancelled is not None and cancelled():
                for pending in running:
                    pending.cancel()
                problems.append("cancelled")
                break
            try:
                resources[path] = finished.result()
            except Exception as exc:
                problems.append(f"{path}: {exc}")
    tally = {}
    for kind in sources.values():
        tally[kind] = tally.get(kind, 0) + 1
    if tally:
        print("  resources: " + ", ".join(
            f"{count} {kind}" for kind, count in sorted(tally.items())))
    return document, resources, problems


def write_document(document, destination: Path) -> Path:
    """Writes the document beside the cache, for the builder to read.

    `build_ship` takes a path rather than an object, and one temporary file is
    a smaller change than reworking its signature.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(document), encoding="utf-8")
    return destination

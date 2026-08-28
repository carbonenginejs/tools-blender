"""EVE resource file addressing.

A resource's stored name IS its identity: a hash of its logical path, then the
md5 of its contents.

    <shard>/<16 hex, FNV-1 of the logical path>_<32 hex, md5 of contents>

The shard is the first two characters of that same path hash, not a separate
value. Ported from `runtime/src/global/utils/resFile.js`; see
`docs/architecture/resource-addressing-and-staleness.md`.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


ADDRESS = re.compile(r"^([a-f0-9]{2})/([a-f0-9]{16})_([a-f0-9]{32})$")

#: 64-bit FNV-1 constants.
OFFSET_BASIS = 0xCBF29CE484222325
PRIME = 0x100000001B3
MASK = 0xFFFFFFFFFFFFFFFF


def normalize(logical_path: str) -> str:
    """A logical path in the form its address is computed from.

    Lowercased, with backslashes turned round, as
    `CjsFileIndexEntry.normalizeLogicalPath` does. EVE's paths are
    case-insensitive and the stored address is the hash of the LOWERCASED
    path, so hashing what a document happens to spell means
    `res:/Texture/Global/noise.dds` addresses a file that does not exist while
    the real one sits in a different shard entirely -- and, since it is never
    found, is downloaded again on every single load.
    """

    return str(logical_path or "").replace("\\", "/").lower()


def fnv1_64(logical_path: str) -> str:
    """FNV-1 (64-bit) over a resource's logical path, 16 lowercase hex digits.

    FNV-**1**, not 1a: the multiply happens BEFORE the xor.

    The path is normalized first, so callers do not each have to remember to.

    Only defined for ASCII. Two implementations exist in the wild -- one over
    UTF-8 bytes, one over UTF-16 code units -- and they agree on ASCII and
    disagree beyond it. No real resource path has been non-ASCII, so a
    non-ASCII path raises rather than returning something plausible.
    """

    value = normalize(logical_path)
    digest = OFFSET_BASIS
    for character in value:
        code = ord(character)
        if code > 0x7F:
            raise ValueError(
                f"Resource path hashing is only defined for ASCII: {value!r}")
        digest = (digest * PRIME) & MASK
        digest ^= code
    return f"{digest:016x}"


def address(logical_path: str, md5: str) -> str:
    """`<shard>/<pathHash>_<md5>` for one resource."""

    checksum = str(md5).lower()
    if not re.fullmatch(r"[a-f0-9]{32}", checksum):
        raise ValueError(f"Resource content digest must be 32 hex digits: {md5}")
    path_hash = fnv1_64(logical_path)
    return f"{path_hash[:2]}/{path_hash}_{checksum}"


def parse(stored) -> dict | None:
    """The parts of a stored address, or None when it is not one.

    None rather than raising: an index legitimately carries plain paths for
    overlay entries alongside content-addressed ones.
    """

    found = ADDRESS.match(str(stored).replace("\\", "/").lower())
    if found is None:
        return None
    return {"shard": found.group(1), "path_hash": found.group(2),
            "checksum": found.group(3)}


def md5_of(data: bytes) -> str:
    """The content digest half of an address."""

    return hashlib.md5(data).hexdigest()


def cache_path(root, logical_path: str, md5: str) -> Path:
    """Where one resource is stored under a ResFiles root."""

    return Path(root) / "ResFiles" / address(logical_path, md5)


def find_cached(root, logical_path: str):
    """A cached file for this path whatever its content digest, or None.

    The path half of the address is stable; only the md5 changes when EVE
    changes the file. So the shard and the `<pathHash>_` prefix are enough to
    find what is already stored, without knowing the digest in advance.
    """

    path_hash = fnv1_64(logical_path)
    shard = Path(root) / "ResFiles" / path_hash[:2]
    if not shard.is_dir():
        return None
    for found in shard.glob(f"{path_hash}_*"):
        if found.is_file() and found.stat().st_size > 0:
            return found
    return None

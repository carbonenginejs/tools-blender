"""Removing cached resources no kept build refers to any more.

The cache is content-addressed, so a file that changes upstream arrives under a
NEW name and sits beside the one it replaced. Nothing overwrites and nothing
notices, which means `ResFiles` only ever grows.

The index for a build already answers which files belong to it: every row's
second column IS the stored address, so the union of those columns over the
builds worth keeping is exactly the set to keep. No hashing, no downloads, and
no question put to anybody.

This used to shell out to `cjs-tools-cache-prune.js` in a tools-core checkout.
It does not any more: an artist installs a zip, and a feature that needs Node
and a git clone is a feature they do not have.
"""

from __future__ import annotations

from pathlib import Path
import re

from . import resfile


class PruneError(RuntimeError):
    """Raised when a prune would be guesswork rather than an answer."""


#: `resfileindex-<build>.txt`, as `resindex.index_file` writes them.
INDEX_NAME = re.compile(r"^resfileindex-(\d+)\.txt$", re.IGNORECASE)


def cached_builds(cache_root) -> list:
    """Every build with an index on disk, newest first.

    Sorted numerically rather than as text, or build 999999 outranks 1000000.
    """

    folder = Path(cache_root) / "indexes"
    if not folder.is_dir():
        return []
    builds = []
    for found in folder.iterdir():
        name = INDEX_NAME.match(found.name)
        if name is not None and found.is_file():
            builds.append(name.group(1))
    return sorted(builds, key=int, reverse=True)


def addresses_in(index_path) -> set:
    """The stored addresses one index names.

    Only the content-addressed rows: an index also carries plain paths for
    overlay entries, and those are not files in `ResFiles`.
    """

    found = set()
    with open(index_path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            columns = line.split(",", 2)
            if len(columns) < 2:
                continue
            parsed = resfile.parse(columns[1].strip())
            if parsed is not None:
                found.add(f"{parsed['shard']}/{parsed['path_hash']}_"
                          f"{parsed['checksum']}")
    return found


def plan(cache_root, keep_latest: int = 1) -> dict:
    """What a prune would keep and what it would remove.

    Separate from doing it, so the operator can say "1,204 files, 310 MB"
    before anything is deleted and so the decision can be tested without a
    filesystem full of real resources.

    Refuses rather than guesses when no index is on disk: with nothing to
    compare against, every cached file looks unreferenced, and "delete
    everything" is not a prune.
    """

    root = Path(cache_root)
    builds = cached_builds(root)
    if not builds:
        raise PruneError("no build index is cached, so nothing can be "
                         "identified as unused")

    keep = max(1, int(keep_latest))
    kept_builds = builds[:keep]
    wanted = set()
    for build in kept_builds:
        wanted |= addresses_in(root / "indexes" / f"resfileindex-{build}.txt")

    remove, bytes_freed, kept_files = [], 0, 0
    resfiles = root / "ResFiles"
    if resfiles.is_dir():
        for shard in resfiles.iterdir():
            if not shard.is_dir():
                continue
            for found in shard.iterdir():
                if not found.is_file():
                    continue
                # The stored name carries an extension, and a decoded copy
                # sits beside its source under the SAME name with a different
                # one. Matching on the address alone keeps both, and drops
                # both when the build they belong to goes.
                address = f"{shard.name}/{found.name.split('.')[0]}"
                if address in wanted:
                    kept_files += 1
                    continue
                remove.append(found)
                bytes_freed += found.stat().st_size

    return {
        "builds": builds,
        "kept_builds": kept_builds,
        "dropped_builds": builds[keep:],
        "kept": kept_files,
        "remove": remove,
        "bytes": bytes_freed,
    }


def prune(cache_root, keep_latest: int = 1, *, apply: bool = False) -> dict:
    """Plans a prune and, with `apply`, carries it out.

    Indexes for builds no longer kept go too -- keeping one would make the
    next prune protect files this one just deleted.
    """

    decided = plan(cache_root, keep_latest)
    if not apply:
        return decided

    removed = 0
    for found in decided["remove"]:
        try:
            found.unlink()
            removed += 1
        except OSError:
            pass                        # a file in use is not worth failing on

    for build in decided["dropped_builds"]:
        index = Path(cache_root) / "indexes" / f"resfileindex-{build}.txt"
        try:
            index.unlink()
        except OSError:
            pass

    # An empty shard left behind is harmless but reads as if files are there.
    resfiles = Path(cache_root) / "ResFiles"
    if resfiles.is_dir():
        for shard in resfiles.iterdir():
            if shard.is_dir() and not any(shard.iterdir()):
                try:
                    shard.rmdir()
                except OSError:
                    pass

    decided["removed"] = removed
    return decided

"""The weapons a hull can carry, and where each one's model lives.

Nothing here is derived from a name or a type id. The service keeps a weapons
library and every entry names its own `resPath` -- the `.black` the engine
mounts -- so this fetches and filters rather than guessing:

    /<target>/<build>/weapons        -> 784 types, each with resPath and slot
    /resources/<path>?format=json    -> EveTurretSet: geometry, effect, locator

The `slot` is the library's, not ours. Deriving it from the path was tried in
another consumer and got the extra-large turrets wrong -- 147 against the
library's 72 -- so the field is read and never recomputed.

No ``bpy`` import; testable with the standard library.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


#: Held per (target, build): the library is 1.5MB and changes only when EVE
#: does, so it is fetched once and kept.
_CACHE: dict = {}

#: Where the library is kept between sessions, set by `service_access`.
CACHE_ROOT = {"path": None}

WEAPONS_FILE = "carbon-weapons-{target}-{build}.json"

#: Which slots hold something that mounts on a turret locator.
TURRET_SLOTS = ("turrets", "xlTurrets")

#: `res:/dx9/model/turret/<family>/...` -- for grouping the list, and for
#: nothing else. What a weapon IS comes from its `slot`.
FAMILY = re.compile(r"^res:/dx9/model/turret/([^/]+)/", re.IGNORECASE)


def _stored(target: str, build: str):
    root = CACHE_ROOT.get("path")
    return Path(root) / WEAPONS_FILE.format(target=target, build=build) if root \
        else None


def _english(name) -> str:
    """One readable name. The library carries eight languages per weapon."""

    if isinstance(name, dict):
        return str(name.get("en") or next(iter(name.values()), ""))
    return str(name or "")


def catalogue(client, *, build: str = "latest", target: str = "eve",
              slots=TURRET_SLOTS) -> list:
    """`[{typeID, name, resPath, slot, family, ...}]`, sorted by name."""

    key = (target, build, "weapons")
    rows = _CACHE.get(key)

    if rows is None:
        path = _stored(target, build)
        if path is not None and path.is_file():
            try:
                rows = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                rows = None

    if rows is None:
        if client is None:
            return []
        try:
            answer = client.request_json("GET", f"/{target}/{build}/weapons")
        except Exception:
            return []
        types = (answer or {}).get("types") or {}
        source = types.values() if isinstance(types, dict) else types
        rows = []
        for entry in source:
            res_path = str(entry.get("resPath") or "")
            if not res_path:
                continue
            found = FAMILY.match(res_path)
            rows.append({
                "typeID": int(entry.get("typeID") or 0),
                "name": _english(entry.get("name")),
                "resPath": res_path,
                "slot": str(entry.get("slot") or ""),
                "family": found.group(1).lower() if found else "",
                "techLevel": int(entry.get("techLevel") or 0),
                "metaLevel": int(entry.get("metaLevel") or 0),
                "published": bool(entry.get("published")),
            })
        rows.sort(key=lambda row: row["name"])
        _CACHE[key] = rows

        path = _stored(target, build)
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(rows), "utf-8")
            except OSError:
                pass                     # the cache is a courtesy, not a need
    else:
        _CACHE[key] = rows

    if not slots:
        return list(rows)
    wanted = set(slots)
    return [row for row in rows if row["slot"] in wanted]


def families(rows) -> list:
    """The families present, in a stable order."""

    return sorted({row["family"] for row in rows if row["family"]})


def turret_document(client, res_path: str, *, build: str = "latest",
                    target: str = "eve") -> dict:
    """One turret's `EveTurretSet`, or an empty dict.

    It names the geometry, the effect its meshes are drawn with -- a `quadv5`,
    the same family the hull uses -- and the locator prefix it mounts to.
    """

    key = (target, build, "turret", res_path)
    if key in _CACHE:
        return _CACHE[key]
    if client is None or not res_path:
        return {}

    route = res_path.replace("res:/", "").lstrip("/")
    try:
        answer = client.request_json(
            "GET", f"/{target}/{build}/resources/{route}?format=json")
    except Exception as exc:
        print(f"[CarbonEngineJS SOF] turret unavailable {res_path}: {exc}")
        return {}

    found = (answer or {}).get("object") or answer or {}
    if not isinstance(found, dict):
        return {}
    _CACHE[key] = found
    return found


def forget():
    """Drops the held library, for when the build or service changes."""

    _CACHE.clear()

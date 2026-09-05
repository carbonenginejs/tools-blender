"""The weapons a hull can carry, and where each one's model lives.

Nothing here is derived from a name or a type id. The service keeps a weapons
library and every entry names its own `resPath` -- the `.black` the engine
mounts -- so this fetches and filters rather than guessing:

    /<target>/<build>/weapons        -> 784 types, each with resPath and slot
    /resources/<path>?format=json    -> EveTurretSet: geometry, effect, locator

The `slot` is the weapon's natural family, not the whole compatibility rule.
An XL hardpoint accepts every XL weapon, including launchers and atomics whose
library slot remains `launchers` or `atomics`. Size is therefore normalized
once here and shared by every caller that filters a bay.

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

WEAPONS_FILE = "carbon-weapons-v2-{target}-{build}.json"

#: Authored locator prefix, natural library collection, and UI label. Keeping
#: this in the bpy-free catalogue makes ship construction and fitting share one
#: taxonomy instead of maintaining parallel lists.
WEAPON_KINDS = (
    ("turret", "turrets", "Turrets"),
    ("xl", "xlTurrets", "XL Turrets"),
    ("launcher", "launchers", "Launchers"),
    ("bomb", "bombs", "Bombs"),
    ("atomic", "atomics", "Atomics"),
    ("chain", "chains", "Chains"),
)
WEAPON_SLOTS = tuple(slot for _kind, slot, _label in WEAPON_KINDS)

#: `res:/dx9/model/turret/<family>/...` -- for grouping the list, and for
#: nothing else. What a weapon IS comes from its `slot`.
FAMILY = re.compile(r"^res:/dx9/model/turret/([^/]+)/", re.IGNORECASE)

SIZES = {1: "SMALL", 2: "MEDIUM", 3: "LARGE", 4: "XL"}

#: Launcher groups carry the size that their types omit. Ordered so a longer
#: name wins before the shorter word it contains: Rapid Heavy is large while
#: Heavy is medium, and Rapid Light is medium while Light is small.
LAUNCHER_SIZES = (
    (4, (("xl",), ("rapid", "torpedo"))),
    (3, (("rapid", "heavy"), ("cruise",), ("torpedo",))),
    (2, (("rapid", "light"), ("heavy",))),
    (1, (("light",), ("rocket",), ("bomb",), ("defender",))),
)


def _stored(target: str, build: str):
    root = CACHE_ROOT.get("path")
    return Path(root) / WEAPONS_FILE.format(target=target, build=build) if root \
        else None


def _english(name) -> str:
    """One readable name. The library carries eight languages per weapon."""

    if isinstance(name, dict):
        return str(name.get("en") or next(iter(name.values()), ""))
    return str(name or "")


def size_of(entry, group_name: str = ""):
    """A weapon's size from shared data or a legacy launcher group name."""

    value = entry.get("chargeSize")
    if value in (None, ""):
        value = entry.get("size")
    if value not in (None, ""):
        if str(value).upper() in SIZES.values():
            return next(key for key, label in SIZES.items()
                        if label == str(value).upper())
        try:
            return int(value)
        except (TypeError, ValueError):
            pass

    words = set(re.findall(r"[a-z0-9]+", str(group_name).lower()))
    for size, alternatives in LAUNCHER_SIZES:
        if any(all(word in words for word in phrase)
               for phrase in alternatives):
            return size
    return None


def compatible_slots(entry, group_name: str = "") -> tuple:
    """The bays that accept a weapon, preferring tools-core's shared answer."""

    authored = entry.get("compatibleSlots")
    if isinstance(authored, (list, tuple)) and authored:
        return tuple(str(slot) for slot in authored if slot)

    natural = str(entry.get("slot") or "")
    result = [natural] if natural else []
    if size_of(entry, group_name) == 4 and "xlTurrets" not in result:
        result.append("xlTurrets")
    return tuple(result)


def fits_slot(entry, slot: str) -> bool:
    """Whether one weapon can be mounted in one runtime weapon slot."""

    return slot in compatible_slots(entry)


def catalogue(client, *, build: str = "latest", target: str = "eve",
              slots=WEAPON_SLOTS) -> list:
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
        groups = (answer or {}).get("groups") or {}
        source = types.values() if isinstance(types, dict) else types
        rows = []
        for entry in source:
            res_path = str(entry.get("resPath") or "")
            if not res_path:
                continue
            found = FAMILY.match(res_path)
            group_id = int(entry.get("groupID") or 0)
            group = (groups.get(str(group_id)) or groups.get(group_id) or {}) \
                if isinstance(groups, dict) else {}
            group_name = _english(group.get("name"))
            size = size_of(entry, group_name)
            accepted = compatible_slots(entry, group_name)
            rows.append({
                "typeID": int(entry.get("typeID") or 0),
                "name": _english(entry.get("name")),
                "resPath": res_path,
                "slot": str(entry.get("slot") or ""),
                "family": found.group(1).lower() if found else "",
                "groupID": group_id,
                "group": group_name,
                "chargeSize": entry.get("chargeSize"),
                "size": SIZES.get(size),
                "compatibleSlots": list(accepted),
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
    wanted = {slots} if isinstance(slots, str) else set(slots)
    return [row for row in rows
            if any(fits_slot(row, slot) for slot in wanted)]


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

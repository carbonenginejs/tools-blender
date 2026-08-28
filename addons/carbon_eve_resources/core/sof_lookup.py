"""Ship name, type id and skin id -> the DNA that draws them.

    name  -> type id / skin id      `/<target>/<build>/skin/names`
    type  -> hull, faction, race    `/types/{id}` -> `/sde/graphics/{graphicId}`
    skin  -> materials or pattern   `/skin/skins` -> skinMaterials -> materialSets

The SOF names live on the GRAPHIC, not the type. A skin's material set carries
`material1..4`, `sofPatternName`, `patternMaterial1..2`, `resPathInsert` and a
faction -- the DNA commands a SKIN amounts to.

No ``bpy`` import; testable with the standard library.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from . import sof_resolution


#: Cached per (target, build). The name index is 57604 entries and the skin
#: tables are thousands more, so each is fetched once and held.
_CACHE: dict[tuple, Any] = {}


def _fetch(client, route: str, key: tuple):
    if key in _CACHE:
        return _CACHE[key]
    if client is None:
        return None
    try:
        value = client.request_json("GET", route)
    except Exception:
        return None
    _CACHE[key] = value
    return value


#: Where the name index is kept between sessions, set by `service_access`.
CACHE_ROOT = {"path": None}

#: Only what can be drawn. The full index is 57604 names and 6.4MB; the ones
#: with a graphic or a skin are 24655 and 1.1MB written compactly, and the rest
#: are blueprints and modules nobody can load.
NAMES_FILE = "carbon-names-{target}-{build}.json"


#: The categories worth offering: 6 Ship, 65 Structure.
#:
#: A hull's GRAPHIC is shared by everything modelled on it, so filtering on
#: "has a graphic" also answers with that hull's blueprints, the NPC entities
#: flying it and a wreck of it -- all carrying the ship's name.
#: `docs/contracts/dna-reverse-index.md` is explicit that `published` alone does
#: not separate them, and the category does.
DRAWABLE_CATEGORIES = (6, 65)

#: The SDE pages at a thousand rows, and there are ~1610 groups.
SDE_PAGE = 1000


def ship_groups(client=None, *, build: str = "latest", target: str = "eve") -> set:
    """Group ids whose category is one a person would want to load."""

    key = (target, build, "shipGroups")
    if key in _CACHE:
        return _CACHE[key]
    if client is None:
        return set()

    found = set()
    offset = 0
    while True:
        try:
            page = client.request_json(
                "GET",
                f"/{target}/{build}/sde/groups?limit={SDE_PAGE}&offset={offset}")
        except Exception:
            return set()
        items = (page or {}).get("items") or []
        for item in items:
            payload = item.get("payload") or {}
            if payload.get("categoryID") in DRAWABLE_CATEGORIES:
                try:
                    found.add(int(item.get("id")))
                except (TypeError, ValueError):
                    continue
        offset += len(items)
        if not items or offset >= int((page or {}).get("rowCount") or 0):
            break
    _CACHE[key] = found
    return found


def names(client=None, *, build: str = "latest", target: str = "eve") -> dict:
    """The name index: a lowercased name -> what it can refer to.

    One name can mean several things, so every entry is a list.

    Cached on disk per BUILD, because it is 6.4MB and it only changes when EVE
    does. Re-downloading it every session is most of what a cold start costs.
    """

    key = (target, build, "names")
    if key in _CACHE:
        return _CACHE[key]

    path = _names_path(target, build, client)
    if path is not None and path.is_file():
        try:
            found = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(found, Mapping):
                _CACHE[key] = found
                return found
        except (OSError, ValueError):
            pass                       # a bad cache file is not worth keeping

    found = _fetch(client, f"/{target}/{build}/skin/names", key)
    if not isinstance(found, Mapping):
        return {}
    groups = ship_groups(client, build=build, target=target)
    drawable = {}
    for name, entries in found.items():
        kept = [entry for entry in entries
                if entry.get("kind") == "skin"
                or (entry.get("graphicID")
                    and (not groups or entry.get("groupID") in groups))]
        if kept:
            drawable[name] = kept
    _CACHE[key] = drawable
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(drawable, separators=(",", ":")),
                            encoding="utf-8")
        except OSError:
            pass                       # an unwritable cache is not an error
    return drawable


def _names_path(target: str, build: str, client=None):
    """Where the index is cached, under an EXACT build.

    Never under `latest`: that names a moving target, so the file would still
    be served after EVE moved on and nothing would ever refetch it.
    """

    root = CACHE_ROOT.get("path")
    if not root:
        return None
    exact = str(build or "")
    if exact in ("", "latest"):
        answer = _fetch(client, f"/{target}/latest/build", (target, "latest", "build"))
        exact = str((answer or {}).get("build") or "")
        if not exact:
            return None
    return Path(root) / NAMES_FILE.format(target=target, build=exact)


def find(name: str, client=None, *, kind: str = "", build: str = "latest",
         target: str = "eve") -> list:
    """Everything one name refers to, optionally of one kind only.

    Matched lowercased, which is how the index is keyed.
    """

    wanted = str(name or "").strip().lower()
    if not wanted:
        return []
    entries = names(client, build=build, target=target).get(wanted) or []
    if kind:
        entries = [entry for entry in entries if entry.get("kind") == kind]
    return list(entries)


def type_record(type_id, client=None, *, build: str = "latest",
                target: str = "eve") -> dict:
    """One type, which carries the graphic id but not the SOF names."""

    try:
        wanted = int(type_id)
    except (TypeError, ValueError):
        return {}
    if wanted <= 0:
        return {}
    found = _fetch(client, f"/{target}/{build}/types/{wanted}",
                   (target, build, "type", wanted))
    return found if isinstance(found, Mapping) else {}


def graphic_record(graphic_id, client=None, *, build: str = "latest",
                   target: str = "eve") -> dict:
    """One graphic, which is where the SOF names actually live.

    SDE routes wrap the row in a `payload`; unwrapped here.
    """

    try:
        wanted = int(graphic_id)
    except (TypeError, ValueError):
        return {}
    if wanted <= 0:
        return {}
    found = _fetch(client, f"/{target}/{build}/sde/graphics/{wanted}",
                   (target, build, "graphic", wanted))
    if not isinstance(found, Mapping):
        return {}
    payload = found.get("payload")
    return payload if isinstance(payload, Mapping) else found


def type_components(type_id, client=None, *, build: str = "latest",
                    target: str = "eve") -> dict:
    """The hull, faction and race a type draws with, or an empty dict.

    Empty rather than partial: a DNA needs all three.
    """

    record = type_record(type_id, client, build=build, target=target)
    graphic = graphic_record(record.get("graphicID"), client,
                             build=build, target=target)
    hull = str(graphic.get("sofHullName") or "")
    faction = str(graphic.get("sofFactionName") or "")
    race = str(graphic.get("sofRaceName") or "")
    if not (hull and faction and race):
        return {}
    return {"hull": hull, "faction": faction, "race": race,
            "name": _name_of(record)}


def _name_of(record: Mapping[str, Any]) -> str:
    """A type's display name, which arrives as `{text, language}`."""

    name = record.get("name")
    if isinstance(name, Mapping):
        return str(name.get("text") or "")
    return str(name or "")


def skin_material_set(skin_id, client=None, *, build: str = "latest",
                      target: str = "eve") -> dict:
    """The material set a skin paints with, through two hops.

    skin -> skinMaterialID -> materialSetID -> the set.
    """

    try:
        wanted = int(skin_id)
    except (TypeError, ValueError):
        return {}
    skins = _fetch(client, f"/{target}/{build}/skin/skins", (target, build, "skins"))
    materials = _fetch(client, f"/{target}/{build}/skin/skinMaterials",
                       (target, build, "skinMaterials"))
    sets = _fetch(client, f"/{target}/{build}/skin/skinMaterialSets",
                  (target, build, "skinMaterialSets"))
    if not all(isinstance(value, Mapping) for value in (skins, materials, sets)):
        return {}

    skin = skins.get(str(wanted)) or skins.get(wanted)
    if not isinstance(skin, Mapping):
        return {}
    material = (materials.get(str(skin.get("skinMaterialID")))
                or materials.get(skin.get("skinMaterialID")))
    if not isinstance(material, Mapping):
        return {}
    found = (sets.get(str(material.get("materialSetID")))
             or sets.get(material.get("materialSetID")))
    return dict(found) if isinstance(found, Mapping) else {}


def skin_applies(skin_id, type_id, client=None, *, build: str = "latest",
                 target: str = "eve") -> bool:
    """Whether a skin can be worn by a type.

    A skin lists the types it belongs to. Without this, changing the type kept
    the old skin's materials.
    """

    try:
        skin, wanted = int(skin_id), int(type_id)
    except (TypeError, ValueError):
        return False
    if skin <= 0 or wanted <= 0:
        return False
    skins = _fetch(client, f"/{target}/{build}/skin/skins", (target, build, "skins"))
    if not isinstance(skins, Mapping):
        return False
    record = skins.get(str(skin)) or skins.get(skin)
    if not isinstance(record, Mapping):
        return False
    types = record.get("types")
    if not isinstance(types, (list, tuple)):
        return False
    return wanted in {int(value) for value in types
                      if isinstance(value, (int, float))}


def _text(record, *names) -> str:
    """The first of several spellings a field may arrive under."""

    for name in names:
        value = record.get(name)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _material(record, *names) -> str:
    """A material name, lowercased, with absence spelled `none`.

    These arrive capitalised -- "None" -- so the comparison has to be made on a
    normalised value or four absences read as four overrides.
    """

    found = _text(record, *names).lower()
    return found or sof_resolution.NONE


def dna_for(type_id=0, skin_id=0, client=None, *, build: str = "latest",
            target: str = "eve") -> str:
    """The DNA a type, or a type wearing a skin, is drawn with.

    The type supplies the hull; the skin supplies the faction, materials or
    pattern, and the respathinsert.
    """

    components = type_components(type_id, client, build=build, target=target)
    if not components:
        return ""

    commands = {}
    material_set = skin_material_set(skin_id, client, build=build, target=target)
    faction = components["faction"]
    if material_set:
        # Field ALIASES, as `CjsToolSde.BuildSkinDna` reads them. The pattern
        # materials are spelled `customMaterial1` in some rows, and a reader
        # that knows only `patternMaterial1` silently drops those skins.
        materials = [_material(material_set, f"material{index}",
                               f"material_{index}")
                     for index in (1, 2, 3, 4)]
        if any(value != sof_resolution.NONE for value in materials):
            # `mesh?`, which is the spelling the canonical builder emits and
            # what live skins are authored with.
            commands["mesh"] = materials

        pattern = [
            _material(material_set, "sofPatternName", "sof_pattern_name"),
            _material(material_set, "patternMaterial1", "pattern_material_1",
                      "customMaterial1", "custommaterial1"),
            _material(material_set, "patternMaterial2", "pattern_material_2",
                      "customMaterial2", "custommaterial2"),
        ]
        if any(value != sof_resolution.NONE for value in pattern):
            commands["pattern"] = pattern

        insert = _text(material_set, "resPathInsert", "res_path_insert")
        if insert:
            commands["respathinsert"] = [insert]
        faction = _text(material_set, "sofFactionName", "sof_faction_name") or faction

    return sof_resolution.compose([components["hull"]], faction,
                                  components["race"], commands)


def forget():
    """Drops the caches, for when the build changes under us."""

    _CACHE.clear()


def name_for(type_id=0, skin_id=0, client=None, *, build: str = "latest",
             target: str = "eve") -> str:
    """What this ship is CALLED: `svipul abyssal glory`, or `svipul`.

    The name index already carries the full thing -- a SKIN's name includes
    the hull it belongs to -- so this is a reverse lookup over what is on disk
    rather than two requests and a join.

    The skin's name wins when there is one, because that is the ship a person
    means. Empty when nothing matches, which is the caller's cue to fall back
    to the DNA.
    """

    wanted_type, wanted_skin = int(type_id or 0), int(skin_id or 0)
    if not wanted_type and not wanted_skin:
        return ""

    index = names(client, build=build, target=target)
    fallback = ""
    for name, entries in index.items():
        for entry in entries:
            if wanted_skin and int(entry.get("skinID") or 0) == wanted_skin:
                return str(name)
            if (not fallback and wanted_type
                    and entry.get("kind") != "skin"
                    and int(entry.get("typeID") or 0) == wanted_type):
                fallback = str(name)
    return fallback


def file_name(name: str) -> str:
    """One of those names as a filename: `svipul_hrada-oki_offender`.

    Lowercased, spaces to underscores, and anything a filesystem argues about
    dropped. Hyphens and dots are kept -- they are part of names like
    `hrada-oki` and removing them makes the ship harder to recognise, which is
    the whole reason for using the name at all.
    """

    import re

    text = str(name or "").strip().lower()
    text = re.sub(r"[^a-z0-9._-]+", "_", text)
    return re.sub(r"_+", "_", text).strip("._-")

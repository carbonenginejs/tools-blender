"""Ship name, type id and skin id -> the DNA that draws them.

A person knows a ship by its NAME. The SOF knows it by a hull, a faction and a
race, and nothing in the DNA says "Tengu" anywhere. Three lookups bridge that,
all of them tools-core routes rather than anything derived here:

    name  -> type id / skin id      `/<target>/<build>/skin/names`
    type  -> hull, faction, race    `/types/{id}` -> `/sde/graphics/{graphicId}`
    skin  -> materials, faction     `/skin/skins` -> skinMaterials -> materialSets

The type route answers with a `graphicID`, and the graphic record is what
actually carries `sofHullName`, `sofFactionName` and `sofRaceName`. That
indirection is EVE's, not ours: a type says what it IS, a graphic says what it
LOOKS like, and two types can share one graphic.

A skin resolves to a material SET, which carries `material1..4`, a
`resPathInsert` and a faction -- exactly the DNA commands a SKIN is. So a skin
is not a separate concept to model: it is a DNA with commands filled in.

No ``bpy`` import, so all of it is testable with the standard library.
"""

from __future__ import annotations

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


def names(client=None, *, build: str = "latest", target: str = "eve") -> dict:
    """The name index: a lowercased name -> what it can refer to.

    One name can mean several things -- a type and a skin can share it -- so
    every entry is a list, and a caller decides which kind it wanted rather
    than being handed the first.
    """

    found = _fetch(client, f"/{target}/{build}/skin/names", (target, build, "names"))
    return found if isinstance(found, Mapping) else {}


def find(name: str, client=None, *, kind: str = "", build: str = "latest",
         target: str = "eve") -> list:
    """Everything one name refers to, optionally of one kind only.

    Matched on the lowercased name because that is how the index is keyed:
    a person typing `Tengu` and a person typing `tengu` mean the same ship.
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

    The SDE routes wrap their row in a `payload`, so the wrapper is unwrapped
    here rather than at every call site -- reading the wrapper as the row is a
    standing trap with these routes.
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

    Empty rather than partial: a DNA needs all three, and two of them plus a
    guess is a DNA that names something that does not exist.
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

    skin -> skinMaterialID -> materialSetID -> the set, which carries
    `material1..4`, a `resPathInsert` and a faction. Those ARE the DNA commands
    a SKIN amounts to.
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

    A skin lists the types it belongs to. Without this check, changing the type
    kept whatever skin was set and produced a Rifter wearing an Abaddon's
    materials -- a DNA that resolves, and draws something that cannot exist.
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


def dna_for(type_id=0, skin_id=0, client=None, *, build: str = "latest",
            target: str = "eve") -> str:
    """The DNA a type, or a type wearing a skin, is drawn with.

    The type supplies the hull, and the skin -- when there is one -- supplies
    the faction, the materials and the respathinsert. A skin's faction wins,
    because that is the point of wearing one.
    """

    components = type_components(type_id, client, build=build, target=target)
    if not components:
        return ""

    commands = {}
    material_set = skin_material_set(skin_id, client, build=build, target=target)
    faction = components["faction"]
    if material_set:
        materials = [str(material_set.get(f"material{index}") or sof_resolution.NONE)
                     for index in (1, 2, 3, 4)]
        if any(value != sof_resolution.NONE for value in materials):
            commands["material"] = materials
        insert = str(material_set.get("resPathInsert") or "")
        if insert:
            commands["respathinsert"] = [insert]
        faction = str(material_set.get("sofFactionName") or faction)

    return sof_resolution.compose([components["hull"]], faction,
                                  components["race"], commands)


def forget():
    """Drops the caches, for when the build changes under us."""

    _CACHE.clear()

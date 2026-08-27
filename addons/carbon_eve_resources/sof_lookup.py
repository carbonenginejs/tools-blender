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

    One name can mean several things, so every entry is a list.
    """

    found = _fetch(client, f"/{target}/{build}/skin/names", (target, build, "names"))
    return found if isinstance(found, Mapping) else {}


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
        # Lowercased before the `none` test: these arrive capitalised.
        materials = [str(material_set.get(f"material{index}") or sof_resolution.NONE).lower()
                     for index in (1, 2, 3, 4)]
        if any(value != sof_resolution.NONE for value in materials):
            commands["material"] = materials

        # A skin can repaint with a PATTERN instead of materials.
        pattern = str(material_set.get("sofPatternName") or "").strip()
        if pattern:
            commands["pattern"] = [
                pattern,
                str(material_set.get("patternMaterial1") or sof_resolution.NONE).lower(),
                str(material_set.get("patternMaterial2") or sof_resolution.NONE).lower(),
            ]

        insert = str(material_set.get("resPathInsert") or "")
        if insert:
            commands["respathinsert"] = [insert]
        faction = str(material_set.get("sofFactionName") or faction)

    return sof_resolution.compose([components["hull"]], faction,
                                  components["race"], commands)


def forget():
    """Drops the caches, for when the build changes under us."""

    _CACHE.clear()

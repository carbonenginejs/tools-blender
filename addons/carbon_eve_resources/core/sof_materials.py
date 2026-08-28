"""The SOF material catalog: what a slot is called, and what that name holds.

The faction serves its table flattened, keyed `areaType:slotIndex`, falling
back to the primary area as the runtime does. A material is a bag of vec4
parameters.

Does not re-implement the resolution chain.

No ``bpy`` import; testable with the standard library.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from . import sof_resolution


#: How a material's own parameter names map onto a slot's fields. A material
#: carries more than these -- `DustDiffuseColor` among them -- and the extras
#: are left alone rather than guessed at.
PARAMETER_FIELDS = {
    "DiffuseColor": "diffuse",
    "FresnelColor": "fresnel",
    "Gloss": "gloss",
}

#: Cached per (target, build): the catalog is 1149 names and does not change
#: between requests, so re-fetching it per dropdown draw would make the panel
#: hit the service on every redraw.
_CATALOG: dict[tuple, tuple] = {}
_MATERIALS: dict[tuple, dict] = {}
_FACTIONS: dict[tuple, dict] = {}


def faction_material_names(faction_record: Mapping[str, Any] | None) -> dict:
    """The faction's `areaType:slot` table, as served.

    tools-core flattens it, the same shape `EveSOFDataMgr` builds.
    """

    area_materials = (faction_record or {}).get("areaMaterials") or {}
    names = area_materials.get("materialNames") or {}
    return dict(names) if isinstance(names, Mapping) else {}


def material_name_for(names: Mapping[str, str], area_type: int, index: int) -> str:
    """The material a faction gives one slot of one area type.

    Falls back to PRIMARY as the runtime does. mde3_t3's sails name only slot
    4, so their other three are primary's. Returns "" when neither names it.
    """

    key = f"{int(area_type)}:{int(index) - 1}"
    found = names.get(key)
    if found:
        return str(found)
    if int(area_type) == sof_resolution.TYPE_PRIMARY:
        return ""
    fallback = names.get(f"{sof_resolution.TYPE_PRIMARY}:{int(index) - 1}")
    return str(fallback or "")


def material_values(material_record: Mapping[str, Any] | None) -> dict:
    """One material's parameters, as the fields a slot shows.

    Every SOF parameter is a vec4, so `Gloss` is read from x.
    """

    parameters = (material_record or {}).get("parameters") or {}
    if not isinstance(parameters, Mapping):
        return {}
    values = {}
    for name, field in PARAMETER_FIELDS.items():
        value = parameters.get(name)
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            continue
        numbers = [float(item) for item in value[:4]]
        if not numbers:
            continue
        values[field] = numbers[0] if field == "gloss" else tuple(numbers[:3])
    return values


#: The list routes, by the name a caller asks for. Every one of them answers
#: with an array of names.
CATALOGS = ("materials", "patterns", "hulls", "factions", "races")


def catalog(client=None, *, kind: str = "materials", build: str = "latest",
            target: str = "eve") -> tuple:
    """Every name of one kind, sorted, cached per build.

    Empty when the service cannot be reached; callers fall back to a plain
    text field.
    """

    wanted = str(kind or "materials")
    if wanted not in CATALOGS:
        raise ValueError(f"unknown catalog: {kind}")
    key = (target, build, wanted)
    if key in _CATALOG:
        return _CATALOG[key]
    if client is None:
        return ()
    try:
        # These answer with an ARRAY, so they need the call that tolerates one:
        # `_request` insists on an object root.
        names = client.request_json("GET", f"/{target}/{build}/sof/{wanted}")
    except Exception:
        return ()
    if not isinstance(names, Sequence) or isinstance(names, (str, bytes)):
        return ()
    _CATALOG[key] = tuple(sorted(str(name) for name in names if name))
    return _CATALOG[key]


def material(name: str, client=None, *, build: str = "latest",
             target: str = "eve") -> dict:
    """One named material, cached. Empty when it cannot be fetched."""

    wanted = str(name or "").strip()
    if not wanted:
        return {}
    key = (target, build, wanted)
    if key in _MATERIALS:
        return _MATERIALS[key]
    if client is None:
        return {}
    try:
        record = client._request("GET", f"/{target}/{build}/sof/materials/{wanted}")
    except Exception:
        return {}
    if not isinstance(record, Mapping):
        return {}
    _MATERIALS[key] = dict(record)
    return _MATERIALS[key]


def faction(name: str, client=None, *, build: str = "latest",
            target: str = "eve") -> dict:
    """One faction record, cached. Empty when it cannot be fetched."""

    wanted = str(name or "").strip()
    if not wanted:
        return {}
    key = (target, build, wanted)
    if key in _FACTIONS:
        return _FACTIONS[key]
    if client is None:
        return {}
    try:
        record = client._request("GET", f"/{target}/{build}/sof/factions/{wanted}")
    except Exception:
        return {}
    if not isinstance(record, Mapping):
        return {}
    _FACTIONS[key] = dict(record)
    return _FACTIONS[key]


def forget():
    """Drops the caches, for when the build changes under us."""

    _CATALOG.clear()
    _MATERIALS.clear()
    _FACTIONS.clear()

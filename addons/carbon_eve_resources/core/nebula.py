"""Which nebula a place in EVE sits in, and where its cube lives.

A nebula belongs to the REGION, not the system: every system in The Forge sees
the same sky, which the service confirms by reporting `fromRegionID` on a
system's derived nebula. So the choice offered is the region -- 114 named ones
against 8490 systems, all of them saying the same thing.

    /map/regions              -> id, name, nebulaID
    /sde/graphics/{nebulaID}  -> graphicFile, a `.red`
    same stem + `.dds`        -> the cube itself

No ``bpy`` import; testable with the standard library.
"""

from __future__ import annotations

import json
from pathlib import Path


#: Held per (target, build). 114 regions is one small request, but it is one
#: request per panel redraw if nothing remembers it.
_CACHE: dict[tuple, object] = {}

#: Where the region list is kept between sessions, set by `service_access`.
CACHE_ROOT = {"path": None}

REGIONS_FILE = "carbon-regions-{target}-{build}.json"


def _stored(target: str, build: str):
    root = CACHE_ROOT.get("path")
    if not root:
        return None
    return Path(root) / REGIONS_FILE.format(target=target, build=build)


def regions(client, *, build: str = "latest", target: str = "eve"):
    """`[(id, name, nebula id)]`, sorted by name.

    Regions without a nebula are dropped: they have no sky to offer.
    """

    key = (target, build, "regions")
    if key in _CACHE:
        return _CACHE[key]

    path = _stored(target, build)
    if path is not None and path.is_file():
        try:
            # Sorted on the way IN as well as out, so a file written by an
            # older version cannot pin the order the picker shows.
            found = sorted((tuple(row) for row
                            in json.loads(path.read_text("utf-8"))),
                           key=lambda row: row[1])
            _CACHE[key] = found
            return found
        except (OSError, ValueError, IndexError):
            pass                         # a bad cache file is not an error

    if client is None:
        return []
    try:
        rows = client.request_json("GET", f"/{target}/{build}/map/regions")
    except Exception:
        return []

    found = sorted(
        ((int(row["id"]), str(row.get("name") or row["id"]),
          int(row["nebulaID"]))
         for row in (rows or [])
         if row.get("nebulaID")),
        key=lambda row: row[1])          # by NAME: this is a picker, not a table
    _CACHE[key] = found

    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(found), "utf-8")
        except OSError:
            pass                         # the cache is a courtesy, not a need
    return found


def cube_path(client, nebula_id: int, *, build: str = "latest",
              target: str = "eve") -> str:
    """The nebula's cube, as a `res:` path, or "".

    The graphics row names a `.red`; the cube sits beside it with the same
    stem. Note the SDE `payload` wrapper -- the row is not the response.
    """

    key = (target, build, "nebulaCube", int(nebula_id))
    if key in _CACHE:
        return _CACHE[key]
    if client is None:
        return ""

    try:
        row = client.request_json(
            "GET", f"/{target}/{build}/sde/graphics/{int(nebula_id)}")
    except Exception:
        return ""

    graphic = str(((row or {}).get("payload") or {}).get("graphicFile") or "")
    if not graphic:
        return ""
    stem = graphic.rsplit(".", 1)[0] if "." in graphic else graphic
    found = f"{stem}.dds"
    _CACHE[key] = found
    return found


def star_light(client, system_id, *, build: str = "latest",
               target: str = "eve"):
    """`(colour, intensity)` for one system's star, or None.

    The service derives both -- colour from the star's blackbody temperature,
    intensity from its luminosity -- so this reads rather than computes. It is
    per SYSTEM rather than per region, which is why it is separate from the
    nebula: a region has no one star.
    """

    if client is None or not system_id:
        return None
    try:
        system = client.request_json(
            "GET", f"/{target}/{build}/map/systems/{int(system_id)}?expand=all")
    except Exception:
        return None

    light = (((system or {}).get("derived") or {}).get("star") or {}).get("light")
    if not light:
        return None
    colour = tuple(float(value) for value in (light.get("color") or (1, 1, 1)))
    return colour, float(light.get("intensity") or 1.0)


def forget():
    """Drops the held lists, for when the build or service changes."""

    _CACHE.clear()

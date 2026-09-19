"""Blender's selected tools-service source and its separate build domains.

Only metadata is source-specific. Resource bytes remain in the shared,
content-addressed ResFiles store.
"""

from dataclasses import dataclass, asdict
import hashlib
import json
from pathlib import Path

from .writing import ensure_parent


TARGETS = (("eve", "EVE Online", "ccp"),
           ("frontier", "EVE Frontier", "ccp"),
           ("infinity", "Infinity", "netease"),
           ("serenity", "Serenity", "netease"))


@dataclass(frozen=True)
class Source:
    target: str = "eve"
    provider: str = "ccp"
    resource_build: str = "latest"
    sde_build: str = "latest"

    def resources(self):
        return {"target": self.target, "build": self.resource_build}

    def sde(self):
        return {"target": self.target, "build": self.sde_build}

    def to_dict(self):
        return asdict(self)


def resfiles_directory(preferences, target):
    """Optional game-install input; downloaded payloads still share one cache."""
    if not getattr(preferences, "use_local_source", False):
        return None
    field = "frontier_resfiles" if target == "frontier" else "local_resfiles"
    return str(getattr(preferences, field, "") or "").strip() or None


def provider_for(target):
    for name, _, provider in TARGETS:
        if name == target:
            return provider
    raise ValueError(f"Unknown Source: {target}")


def resolve(client, target="eve", cache_root=None):
    provider = provider_for(target)
    path = Path(cache_root) / "sources" / target / "latest.json" if cache_root else None
    try:
        answer = client.request_json("GET", f"/{target}/latest/build")
        builds = answer.get("builds") or {}
        resource = str(builds.get("resources") or answer.get("build") or "")
        sde = str(builds.get("sde") or answer.get("build") or "")
        if not resource.isdecimal() or not sde.isdecimal():
            raise ValueError(f"{target}: service did not report exact builds")
        source = Source(target, provider, resource, sde)
    except Exception:
        if path is None or not path.is_file():
            raise
        source = Source(**json.loads(path.read_text(encoding="utf-8")))
        if source.target != target or source.provider != provider:
            raise ValueError("Cached Source identity does not match selection")
        if not source.resource_build.isdecimal() or not source.sde_build.isdecimal():
            raise ValueError("Cached Source must have exact builds")
        return source
    if path is not None:
        write_json(path, source.to_dict())
    return source


def write_json(path, value):
    ensure_parent(path)
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def resource_resolution(client, cache_root, logical_path, build, target):
    """Pin each logical name to its source/build address, including offline."""
    provider = provider_for(target)
    if not str(build).isdecimal():
        raise ValueError("Resource resolution requires an exact build")
    logical_path = logical_path.lower().replace("\\", "/")
    key = hashlib.sha256(logical_path.lower().encode("utf-8")).hexdigest()
    path = Path(cache_root) / "sources" / target / str(build) / "resolutions" / (key + ".json")
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    result = client.resolve_resource(logical_path, build, target=target, provider=provider)
    resolution = result.get("resolution") or result
    write_json(path, resolution)
    return resolution

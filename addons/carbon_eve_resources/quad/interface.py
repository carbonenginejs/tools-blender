"""The quad family's shader interface, as measured from the shipped containers.

Blender cannot read a compiled Carbon effect, and a hand-transcribed layout
rots the moment EVE ships a shader update. So the interface is *generated* from
the containers and read here as data: which textures each family member binds,
which constants it declares, and what Carbon's own default value for each one
is.

`family.json` is generated, not authored. Regenerate it with the runtime tool:

    node runtime/scripts/resource/formats/dumpEffectInterface.js \\
        <dir-of-sm_depth-containers> \\
        --option SPACE_OBJECT_PPT_ENABLED=SOPPT_ENABLED --json

then reduce each record to the fields below. The tier and permutation are not
incidental: the production body is ``.sm_depth`` with ``SOPPT_ENABLED``, and
the default body of ``.sm_hi`` silently omits dirt, dust, patterns, local
lights and the spherical-harmonic term while remaining a complete, valid,
warning-free shader.

This module has no ``bpy`` dependency so it can be tested with the standard
library alone.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping, Optional, Sequence


SCHEMA = "carbon.quad-family-interface"
SUPPORTED_VERSION = 1
DATA_NAME = "family.json"


class QuadInterfaceError(RuntimeError):
    """Raised when the generated interface data is missing or unusable."""


@dataclass(frozen=True, slots=True)
class Constant:
    """One reflected constant: where it sits, and Carbon's authored default."""

    name: str
    vec4: int
    default: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class Member:
    """One member of the quad family at the production permutation."""

    name: str
    permutation_index: int
    constant_buffer_bytes: int
    textures: tuple[str, ...]
    scene_textures: tuple[str, ...]
    constants: Mapping[str, Constant]

    def constant(self, name: str) -> Optional[Constant]:
        return self.constants.get(name)

    def has(self, *names: str) -> bool:
        """True when every name is a constant or a texture this member binds."""

        return all(
            name in self.constants or name in self.textures or name in self.scene_textures
            for name in names
        )

    def defaults(self) -> dict[str, tuple[float, ...]]:
        """Carbon's authored default for every constant, by name.

        These are what a consumer uses for any value SOF did not resolve.
        Zero is *not* the fallback: a zero-filled material is a black object
        with no gloss, which reads as a broken shader rather than as a missing
        default.
        """

        return {name: constant.default for name, constant in self.constants.items()}


@dataclass(frozen=True, slots=True)
class Family:
    """Every measured member, with the build and permutation they came from."""

    build: str
    tier: str
    permutation: Mapping[str, str]
    members: Mapping[str, Member]

    def member(self, shader: str) -> Optional[Member]:
        """Looks a member up by effect file name, for example ``quadv5.fx``.

        Accepts the authored spellings a SOF document uses: a bare name, an
        ``.fx`` suffix, and the ``skinned_``/``unpacked_`` prefixes, which
        change tangent packing and skinning rather than the pixel-stage
        resource set.
        """

        return self.members.get(normalize_shader_name(shader))

    def common_textures(self) -> tuple[str, ...]:
        """Textures every member binds, in the order the base member binds them."""

        if not self.members:
            return ()
        shared = set.intersection(*(set(m.textures) for m in self.members.values()))
        base = self.members.get("quadv5")
        order = base.textures if base else next(iter(self.members.values())).textures
        return tuple(name for name in order if name in shared)


PREFIXES = ("unpackedskinned_", "unpacked_", "skinned_", "static_")


def normalize_shader_name(shader: str) -> str:
    """Reduces an authored effect name to the family member it selects."""

    name = str(shader or "").strip().lower()
    name = name.rsplit("/", 1)[-1]
    if name.endswith(".fx"):
        name = name[:-3]
    for prefix in PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name


def load_family(path: Optional[Path] = None) -> Family:
    """Reads the generated interface data."""

    source = Path(path) if path else Path(__file__).with_name(DATA_NAME)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise QuadInterfaceError(f"Generated quad interface data is missing: {source}") from error
    except json.JSONDecodeError as error:
        raise QuadInterfaceError(f"Quad interface data is not valid JSON: {source}") from error

    if raw.get("schema") != SCHEMA:
        raise QuadInterfaceError(f"{source} is not {SCHEMA} data")
    version = raw.get("version")
    if version != SUPPORTED_VERSION:
        # Refuse a newer document rather than understanding part of it, which is
        # the rule the SOF bundle reader already follows.
        raise QuadInterfaceError(
            f"{source} is version {version}; this add-on understands {SUPPORTED_VERSION}"
        )

    members = {
        name: _member(name, entry)
        for name, entry in (raw.get("members") or {}).items()
    }
    return Family(
        build=str(raw.get("build", "")),
        tier=str(raw.get("tier", "")),
        permutation=dict(raw.get("permutation") or {}),
        members=members,
    )


def _member(name: str, entry: Mapping) -> Member:
    constants = {
        constant_name: Constant(
            name=constant_name,
            vec4=int(value["vec4"]),
            default=tuple(float(number) for number in value["default"]),
        )
        for constant_name, value in (entry.get("constants") or {}).items()
    }
    return Member(
        name=name,
        permutation_index=int(entry.get("permutationIndex", 0)),
        constant_buffer_bytes=int(entry.get("constantBufferBytes", 0)),
        textures=tuple(_strings(entry.get("textures"))),
        scene_textures=tuple(_strings(entry.get("sceneTextures"))),
        constants=constants,
    )


def _strings(values: Optional[Sequence]) -> list[str]:
    return [str(value) for value in (values or [])]

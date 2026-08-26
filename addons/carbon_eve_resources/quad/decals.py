"""Decal geometry and materials, from a SOF document.

A decal in EVE is not a surface of its own. `EveSpaceObjectDecal` names a subset
of the HULL's triangles through `staticIndexBuffers`, and the shader re-draws
just those with the decal projected onto them. So building one in Blender means
copying those triangles into their own mesh rather than adding geometry.

The projection is the same convention the quad patterns use -- rows 1 and 2 of
the inverse matrix, over a `[-1, 1]` box -- which is why this module needs so
little of its own:

    decalUV = (dot(p, row1), dot(p, row2)) * 0.5 + 0.5

Every decal map is sampled with CLAMP_TO_BORDER against black, so a decal
contributes nothing outside its own projection. Blender's `CLIP` extension is
that, natively, so none of WebGL's emulation is needed.

`decalv5` is lit as part of the hull: it reads the hull's own normal, dirt and
dust maps at the mesh UV while its own maps come from the projected UV. The
other three are simpler -- glow, counter and hole.

The mesh-building half needs ``bpy``; the index and transform handling does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


#: Which decal shaders exist, and what each one is.
DECAL_SHADERS = {
    "decalv5.fx": "lit surface sharing the hull's dirt and normals",
    "decalglowv5.fx": "additive glow, with texture scaling and offset",
    "decalcounterv5.fx": "a counter, offset into a digit strip",
    "decalholev5.fx": "a hull breach, showing an interior cube",
}

#: Decal textures, and whether each is colour or data. Only the albedo and the
#: fresnel maps carry colour; the rest are masks and vectors.
DECAL_TEXTURES = {
    "DecalAlbedoMap": True,
    "DecalFresnelMap": True,
    "DecalGlowMap": True,
    "DecalTransparencyMap": False,
    "DecalNormalMap": False,
    "DecalRoughnessMap": False,
    "DecalHoleMap": False,
    "DecalInsideCubeMap": False,
}

#: How far along the surface normal a decal's copied triangles are lifted.
#:
#: Carbon does not offset in space at all -- it biases in depth, adding 1e-5 to
#: the clip-space z. Blender has no equivalent per-material bias for EEVEE and
#: Cycles alike, so the geometry moves instead, by a fraction of the hull's own
#: size rather than a fixed distance: a frigate and a titan need very different
#: absolute offsets to beat the same z-fighting.
DECAL_LIFT_FRACTION = 0.0004


@dataclass(frozen=True, slots=True)
class Decal:
    """One `EveSpaceObjectDecal`, projected onto a subset of hull triangles."""

    index: int
    shader: str
    position: tuple
    rotation: tuple
    scaling: tuple
    parent_bone: int
    triangles: tuple
    textures: dict
    constants: dict

    @property
    def name(self) -> str:
        return f"decal{self.index:02d} {self.shader.replace('.fx', '')}"

    @property
    def is_lit(self) -> bool:
        """`decalv5` is shaded with the hull; the rest are simpler."""

        return self.shader == "decalv5.fx"


def triangles_from_buffers(buffers: Optional[Sequence]) -> tuple:
    """Flattens `staticIndexBuffers` into triangles.

    The buffers are index runs into the hull's own vertices, three per triangle.
    A decal carries several -- a Legion's first has seven -- and they are taken
    together rather than as alternatives.
    """

    triangles = []
    for buffer in buffers or []:
        indices = list(buffer or [])
        for start in range(0, len(indices) - 2, 3):
            triangles.append((indices[start], indices[start + 1], indices[start + 2]))
    return tuple(triangles)


def read_decals(document) -> list:
    """Every decal in a SOF document, in order."""

    found = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if node.get("_type") == "EveSpaceObjectDecal":
                found.append(node)
            for value in node.values():
                walk(value)

    walk(document)

    decals = []
    for index, node in enumerate(found):
        effect = node.get("decalEffect") or {}
        shader = str(effect.get("effectFilePath", "")).rsplit("/", 1)[-1].lower()
        decals.append(Decal(
            index=index,
            shader=shader,
            position=tuple(node.get("position") or (0.0, 0.0, 0.0)),
            rotation=tuple(node.get("rotation") or (0.0, 0.0, 0.0, 1.0)),
            scaling=tuple(node.get("scaling") or (1.0, 1.0, 1.0)),
            parent_bone=int(node.get("parentBoneIndex", -1)),
            triangles=triangles_from_buffers(node.get("staticIndexBuffers")),
            textures={
                str(r.get("name")): str(r.get("resourcePath"))
                for r in (effect.get("resources") or [])
            },
            constants={
                str(c.get("name")): tuple(c.get("value") or ())
                for c in (effect.get("constParameters") or [])
            },
        ))
    return decals


def summarise(decals: Sequence[Decal]) -> str:
    counts = {}
    triangles = 0
    for decal in decals:
        counts[decal.shader] = counts.get(decal.shader, 0) + 1
        triangles += len(decal.triangles)
    parts = ", ".join(f"{name.replace('.fx', '')} x{count}" for name, count in sorted(counts.items()))
    return f"{len(decals)} decals ({parts}), {triangles} triangles"

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
    #: The SOF's own name and visibility group, when the document carries them.
    #:
    #: A BUILT EveShip2 document does not: it keeps position, rotation, scaling,
    #: the effect and the index buffers, and drops the identity the hull's
    #: `decalSets` gave each one. So these are usually empty here and get filled
    #: in when the source SOF is at hand -- the structure they feed is the same
    #: either way, which is the point of carrying them rather than inventing a
    #: name at the point of use.
    sof_name: str = ""
    visibility_group: str = ""
    #: The DECAL SET this belongs to. A set is the named group a consumer sees,
    #: and it carries the visibility group the client switches on.
    set_name: str = ""

    @property
    def name(self) -> str:
        """The object name: the SOF's if we have it, else index and shader."""

        if self.sof_name:
            return self.sof_name
        return f"decal{self.index:02d} {self.shader.replace('.fx', '')}"

    @property
    def group(self) -> str:
        """The collection this decal belongs in, under `decals`.

        The SOF's own SET name when we have it: that is the named group a
        consumer recognises, and the collection carries the set's visibility
        group as a property beside it.

        Falling back to the shader family keeps damage, glows, counters and
        hull decals apart, which is the best that can be done from a built
        document alone -- it carries neither name.
        """

        return self.set_name or self.visibility_group or self.shader.replace(".fx", "")

    @property
    def is_lit(self) -> bool:
        """`decalv5` is shaded with the hull; the rest are simpler."""

        return self.shader == "decalv5.fx"


def triangles_from_buffers(buffers: Optional[Sequence], lod: int = 0) -> tuple:
    """The triangles of ONE level of detail from `staticIndexBuffers`.

    The buffers are alternatives, not parts: each is a complete index run for
    one LOD, and they shrink down the chain -- a Legion decal's seven run
    48, 45, 39, 36, 33, 24, 21 indices. ccpwgl selects one with
    `GetCurrentIndexBuffer`, and level 0 is the most detailed.

    Taking them all together draws every LOD at once, and because the coarser
    ones index different triangles the result is a scatter of stray faces across
    the hull rather than a decal.
    """

    available = list(buffers or [])
    if not available:
        return ()
    indices = list(available[min(lod, len(available) - 1)] or [])
    return tuple(
        (indices[start], indices[start + 1], indices[start + 2])
        for start in range(0, len(indices) - 2, 3)
    )


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
            sof_name=str(node.get("name") or ""),
            visibility_group=str(node.get("visibilityGroup") or ""),
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


#: What each `EveSOFDataHullDecalSetItem.usage` draws with.
#:
#: Read from `EveSOF.js`, which indexes this table by usage and SKIPS the item
#: when there is no name for it. Note that STANDARD and LOGO share `decalv5`:
#: usage alone does not identify a decal, which is why the match below uses the
#: transform as well.
DECAL_USAGE_EFFECTS = (
    "decalv5.fx",              # 0 STANDARD
    "decalcounterv5.fx",       # 1 KILLCOUNTER
    "decalholev5.fx",          # 2 HOLE
    "decalcylindricv5.fx",     # 3 CYLINDRICAL
    "decalglowcylindricv5.fx", # 4 GLOWCYLINDRICAL
    "decalglowv5.fx",          # 5 GLOWSTANDARD
    "decalv5.fx",              # 6 LOGO
)


def _transform_key(rotation, scaling, bone):
    """A key that survives the trip from hull record to built decal.

    POSITION is deliberately absent. A strategic cruiser is assembled from
    several hulls and `EveSOF` accumulates a subsystem offset into each decal's
    position, so the hull item and the decal it became disagree by that offset.
    Rotation, scaling and the bone index are copied across untouched.

    Rounded, because both sides have been through JSON.
    """

    def rounded(values, count, fallback):
        items = list(values or ())
        items += list(fallback)[len(items):]
        return tuple(round(float(value), 4) for value in items[:count])

    return (rounded(rotation, 4, (0.0, 0.0, 0.0, 1.0)),
            rounded(scaling, 3, (1.0, 1.0, 1.0)),
            int(bone if bone is not None else -1))


def name_decals(decals, decal_sets):
    """Gives each built decal the name and visibility group its SOF set has.

    A built `EveSpaceObjectDecal` carries NEITHER: `EveSOF` copies the
    transform, the bone, the effect and the index buffers onto it and leaves the
    set's name and visibility group and the item's name behind. They exist only
    on the hull record, so the two have to be matched up.

    NOT by index. The builder skips sets that fail the visibility test, items
    whose usage has no effect, and logo items when the faction has no matching
    logo set -- so the two lists have different lengths on exactly the hulls
    that skip something, and an index mapping would misname decals silently.

    Matching is by transform and effect instead, and each hull item is consumed
    once, so two decals sharing a transform take the two candidates in order.

    `decal_sets` is a sequence of mappings with `name`, `visibilityGroup` and
    `items`; pass the sets of EVERY hull of a multi-hull ship, in hull order.
    Returns a new list of `Decal`, unmatched ones unchanged.
    """

    candidates = {}
    for decal_set in decal_sets or ():
        group = str((decal_set or {}).get("visibilityGroup") or "primary")
        set_name = str((decal_set or {}).get("name") or "")
        for item in (decal_set or {}).get("items") or ():
            usage = int((item or {}).get("usage") or 0)
            if usage >= len(DECAL_USAGE_EFFECTS):
                continue
            key = _transform_key(item.get("rotation"), item.get("scaling"),
                                 item.get("boneIndex"))
            candidates.setdefault(key + (DECAL_USAGE_EFFECTS[usage],), []).append(
                (set_name, group, str(item.get("name") or "")))

    named = []
    for decal in decals:
        key = _transform_key(decal.rotation, decal.scaling, decal.parent_bone)
        waiting = candidates.get(key + (decal.shader,))
        if not waiting:
            named.append(decal)
            continue
        set_name, group, item_name = waiting.pop(0)
        named.append(Decal(
            index=decal.index, shader=decal.shader, position=decal.position,
            rotation=decal.rotation, scaling=decal.scaling,
            parent_bone=decal.parent_bone, triangles=decal.triangles,
            textures=decal.textures, constants=decal.constants,
            sof_name=item_name or decal.sof_name,
            visibility_group=group or decal.visibility_group,
            set_name=set_name or getattr(decal, "set_name", ""),
        ))
    return named

"""Builds the quad material as a Blender shader node group.

The group is *generated* from the measured interface rather than authored by
hand, so its sockets carry Carbon's own names, panel groups, tooltips, widget
kinds and default values. Nothing here transcribes a constant: the numbers come
from `reference` and the interface data comes from `interface`.

What this reproduces is the material composition -- which layer, what colour,
how rough, how dirty. It does NOT reproduce Carbon's lighting. The sun, the
environment probe, the screen-space shadow and SSAO buffers and the fog have no
counterpart in Cycles, and reproducing them would mean ignoring the scene's own
lights, which is the reason to be in Blender at all. Blender lights the surface;
this decides what the surface is.

Two consequences worth knowing before comparing against a client screenshot:

* The shader evaluates its whole lighting twice, clean and dusty, and blends the
  two LIT results with weights that do not sum to one. Having no two lit results
  to blend, this blends the surface parameters instead. See
  `reference.combine_dirt`.
* The final gamma and sRGB encode are Blender's view transform's job, so they
  are deliberately absent here.

**Material colours are HDR.** EVE materials are authored with components above
one -- three times a normal colour is common -- and nothing in this module
clamps them. Blender's colour sockets hold values above one and preserve them
through a ``.blend`` save and reload; the pipeline is float and linear
throughout, and the view transform tone-maps only on output. Two things to know:
the colour *picker* widget clamps what a user drags even though typed and
scripted values persist, and no socket built here is given a maximum.

This module imports ``bpy``; the arithmetic and the interface data do not.
"""

from __future__ import annotations

from typing import Optional

import bpy

from . import reference
from .interface import Member, load_family, socket_name


GROUP_PREFIX = "Carbon"

#: Sockets the group produces. `BSDF` makes it usable as a material on its own;
#: the rest let a caller light the surface some other way.
OUTPUTS = (
    ("BSDF", "NodeSocketShader", "Ready-to-use surface"),
    ("Albedo", "NodeSocketColor", "Base colour after material, paint and dirt"),
    ("Roughness", "NodeSocketFloat", "Roughness derived from blended gloss"),
    ("Fresnel", "NodeSocketColor", "F0 after material, paint and dirt"),
    ("Normal", "NodeSocketVector", "World-space normal"),
    ("Emission", "NodeSocketColor", "Glow, scaled by activation"),
)

#: Values that belong to the SHIP, not to a material.
#:
#: Age, activation, booster gain, emission strength and the kill count are one
#: per object in Carbon -- they live in the per-object constant buffer -- so
#: every area material and every decal on a hull must see the same value.
#: Exposing them as material sockets meant editing a Legion's age four times
#: and its decals seventeen more.
#:
#: They are read from the shaded object's custom properties through Attribute
#: nodes, the same mechanism the pattern projections use, so one edit reaches
#: everything. The per-MATERIAL heat lanes stay sockets: shimmer speed, size
#: and strength are authored per material and differ between areas.
SHIP_PROPERTIES = {
    "AgeInWeeks": ("carbon_ship_age_weeks", 0.0),
    "Activation": ("carbon_ship_activation", 1.0),
    "BoosterGain": ("carbon_ship_booster_gain", 1.0),
    "EmissionStrength": ("carbon_ship_emission_strength", 1.0),
    "KillCount": ("carbon_ship_kill_count", 0.0),
}

#: The dust noise map's alpha is used separately from its colour, and Blender
#: exposes those as different sockets, so it needs an input of its own.
DUST_ALPHA = "DustNoiseAlpha"

#: Where the pattern controls belong once they exist, decided with the operator.
#:
#: Each projection's own controls -- wrap mode U and V, mirror, the transform,
#: and which base materials it may paint -- go in that projection's existing
#: `Pattern Material 1` / `Pattern Material 2` panel, which comes from Carbon's
#: own `Group` annotation. `Blend Mode` combines the two masks rather than
#: belonging to either, so it goes in `General` or `Object`.
#:
#: Wrap mode is an integer input, 0 REPEAT / 1 EDGE / 2 BORDER: Blender 5.0 has
#: menu sockets but shader trees have no switch node to consume one, and an
#: image node's `extension` is a single setting for both axes while real data
#: mixes them.
PATTERN_PANELS = ("Pattern Material 1", "Pattern Material 2")
PATTERN_SHARED_PANEL = "General"

#: Object custom properties holding one ship's pattern projections.
#:
#: An `EveCustomMask` is per SHIP, not per area -- all four of a Legion's areas
#: share the same two projections -- and in Carbon it lands in the per-object
#: constant buffer. So it lives on the Blender OBJECT and is read with Attribute
#: nodes, which keeps one source of truth for every material on that object and
#: matches where Carbon puts it. `{}` is the mask index.
MASK_PROPERTIES = {
    "position": "carbon_mask{}_position",
    "rotation": "carbon_mask{}_rotation",
    "scaling": "carbon_mask{}_scaling",
    "mirrored": "carbon_mask{}_mirrored",
    "wrap": "carbon_mask{}_wrap",
    "targets": "carbon_mask{}_targets",
    "target4": "carbon_mask{}_target4",
    "material": "carbon_mask{}_material",
    "flip": "carbon_mask{}_flip",
}

#: The projection group's name; one per blend file, shared by every material.
PROJECTION_GROUP = "Carbon Pattern Projection"

#: `materialIndex` selects which material a projection paints with. Measured
#: from the pixel stage's comparisons against 2, 3, 4 and 5:
#:
#:     < 1  Mtl1      3..4  Mtl4
#:     1..2 Mtl2      4..5  PMtl1
#:     2..3 Mtl3      >= 5  PMtl2
#:
#: Real SKINs use 4 and 5 -- a Legion's two masks are exactly that -- so only
#: the pattern materials are implemented here; picking a base material as a
#: pattern source is a documented gap rather than a silent wrong answer.
MATERIAL_INDEX_PMTL1 = 4
MATERIAL_INDEX_PMTL2 = 5



def _new_group(name: str) -> bpy.types.ShaderNodeTree:
    existing = bpy.data.node_groups.get(name)
    if existing:
        bpy.data.node_groups.remove(existing)
    return bpy.data.node_groups.new(name, "ShaderNodeTree")


def _panel(tree, name: str, cache: dict):
    if not name or name == "Other":
        return None
    if name not in cache:
        cache[name] = tree.interface.new_panel(name, default_closed=True)
    return cache[name]


def _socket(tree, name, kind, *, description="", default=None, panel=None,
            min_value=None, max_value=None):
    socket = tree.interface.new_socket(
        name=name, in_out="INPUT", socket_type=kind, parent=panel
    )
    if description:
        socket.description = description
    if default is not None:
        socket.default_value = default
    if min_value is not None:
        socket.min_value = min_value
    if max_value is not None:
        socket.max_value = max_value
    return socket



def drive_ship_values(group_node, obj):
    """Drives a material's per-ship sockets from an object's properties.

    Carbon holds age, activation, booster gain, emission strength and the kill
    count once per OBJECT, so every area of a hull and every decal on it must
    read the same number. An Attribute node would express that directly, but
    EEVEE delivers only eight object attributes per material and returns zero
    for the rest without saying so, and the pattern masks already spend all
    eight and more.

    A driver has no such budget. It reads the same custom property, updates
    live when the operator edits it, and lets a decal point at the HULL rather
    than at itself, which is what "one number per ship" actually means.

    Returns the number of sockets driven.
    """

    driven = 0
    for name, (prop, _) in SHIP_PROPERTIES.items():
        socket = group_node.inputs.get(name)
        if socket is None:
            continue
        index = list(group_node.inputs).index(socket)
        group_node.id_data.driver_remove(f'nodes["{group_node.name}"].inputs[{index}].default_value')
        driver = group_node.id_data.driver_add(
            f'nodes["{group_node.name}"].inputs[{index}].default_value').driver
        driver.type = "SCRIPTED"
        variable = driver.variables.new()
        variable.name = "v"
        variable.targets[0].id_type = "OBJECT"
        variable.targets[0].id = obj
        variable.targets[0].data_path = f'["{prop}"]'
        # A missing property leaves the socket at its own default rather than
        # failing the whole driver, which would render as black.
        driver.expression = "v"
        driven += 1
    return driven

def build_projection_group() -> bpy.types.ShaderNodeTree:
    """Builds the shared pattern-projection group.

    It is a SEPARATE group from the material, and has to be: the projected UV
    has to reach an Image Texture node whose colour then feeds the material, and
    routing that through one group node would be a cycle. So this produces
    coordinates and coverage, the material samples the masks with them, and the
    result goes into the quad group.

    It takes no inputs. Every value comes from the shaded object's custom
    properties through Attribute nodes, so all of a ship's materials read one
    source and a second ship with different masks needs no new material.

    The projection, measured from the vertex stage::

        p  = position, with x folded to |x| when the mask is mirrored
        uv = (dot(p, row0), dot(p, row1)) * 0.5 + 0.5

    which is the mask's inverse transform applied to the object-space position,
    with the resulting [-1, 1] box mapped onto [0, 1].
    """

    tree = _new_group(PROJECTION_GROUP)
    nodes, links = tree.nodes, tree.links

    for index in (1, 2):
        tree.interface.new_socket(
            name=f"UV {index}", in_out="OUTPUT", socket_type="NodeSocketVector"
        ).description = f"Projected coordinates for pattern mask {index}"
        tree.interface.new_socket(
            name=f"Coverage {index}", in_out="OUTPUT", socket_type="NodeSocketFloat"
        ).description = (
            "Zero where a CLAMP_TO_BORDER axis falls outside the projection, "
            "which is the only wrap mode that can cover nothing"
        )

    output = nodes.new("NodeGroupOutput")
    output.location = (900, 0)

    coordinate = nodes.new("ShaderNodeTexCoord")
    coordinate.location = (-1200, 0)

    def attribute(name, row):
        node = nodes.new("ShaderNodeAttribute")
        node.attribute_type = "OBJECT"
        node.attribute_name = name
        node.location = (-1200, row)
        node.label = name
        return node

    for index in (0, 1):
        base = index * -900
        props = {key: pattern.format(index) for key, pattern in MASK_PROPERTIES.items()}

        mirrored = attribute(props["mirrored"], base - 200).outputs["Factor"]

        # Mirroring folds the projection about the object's X = 0 plane: the
        # shader adds (|x| - x), which leaves x alone when positive and negates
        # it when not.
        separate = nodes.new("ShaderNodeSeparateXYZ")
        separate.location = (-1000, base)
        links.new(coordinate.outputs["Object"], separate.inputs[0])

        absolute = nodes.new("ShaderNodeMath")
        absolute.operation = "ABSOLUTE"
        absolute.location = (-840, base + 60)
        links.new(separate.outputs["X"], absolute.inputs[0])

        difference = nodes.new("ShaderNodeMath")
        difference.operation = "SUBTRACT"
        difference.location = (-700, base + 60)
        links.new(absolute.outputs[0], difference.inputs[0])
        links.new(separate.outputs["X"], difference.inputs[1])

        folded = nodes.new("ShaderNodeMath")
        folded.operation = "MULTIPLY_ADD"
        folded.location = (-560, base + 60)
        folded.label = "mirror fold"
        links.new(difference.outputs[0], folded.inputs[0])
        links.new(mirrored, folded.inputs[1])
        links.new(separate.outputs["X"], folded.inputs[2])

        combine = nodes.new("ShaderNodeCombineXYZ")
        combine.location = (-420, base)
        links.new(folded.outputs[0], combine.inputs["X"])
        links.new(separate.outputs["Y"], combine.inputs["Y"])
        links.new(separate.outputs["Z"], combine.inputs["Z"])

        # TEXTURE mapping applies the INVERSE transform, which is what turns an
        # object-space position into the mask's own space.
        mapping = nodes.new("ShaderNodeMapping")
        mapping.vector_type = "TEXTURE"
        mapping.location = (-260, base)
        mapping.label = f"mask {index} inverse transform"
        links.new(combine.outputs[0], mapping.inputs["Vector"])
        links.new(attribute(props["position"], base + 240).outputs["Vector"],
                  mapping.inputs["Location"])
        links.new(attribute(props["rotation"], base + 120).outputs["Vector"],
                  mapping.inputs["Rotation"])
        links.new(attribute(props["scaling"], base - 60).outputs["Vector"],
                  mapping.inputs["Scale"])

        # The [-1, 1] projection box onto [0, 1]. The per-axis flip is a live
        # object property rather than a decision baked in here, because Carbon
        # is row-vector and Blender column-vector and the resulting sign is
        # easier to settle by looking than to derive.
        #
        # Flipping BOTH axes is a 180-degree rotation; flipping ONE is a mirror,
        # and those are different transforms, so all four combinations are
        # reachable. Multiplier per axis is `0.5 - flip`, giving +0.5 unflipped
        # and -0.5 flipped.
        flip = attribute(props["flip"], base - 440).outputs["Vector"]
        flip_parts = nodes.new("ShaderNodeSeparateXYZ")
        flip_parts.location = (-140, base - 440)
        links.new(flip, flip_parts.inputs[0])

        centred = mapping.outputs["Vector"]

        wrap = attribute(props["wrap"], base - 320).outputs["Vector"]
        wrap_parts = nodes.new("ShaderNodeSeparateXYZ")
        wrap_parts.location = (60, base - 320)
        links.new(wrap, wrap_parts.inputs[0])

        uv_parts = nodes.new("ShaderNodeSeparateXYZ")
        uv_parts.location = (60, base)
        links.new(centred, uv_parts.inputs[0])

        wrapped, coverage = [], None
        # The projection uses the transformed position's Y and Z, not X and Y.
        # `customMaskMatrix[2]` occupies vec4 16-23 of the vertex per-object
        # buffer -- confirmed by customMaskData landing at 24-25, where the
        # shader reads isMirrored -- and the vertex stage dots the position with
        # cb3[17]/cb3[18] and cb3[21]/cb3[22], which are rows 1 and 2 of each
        # matrix rather than 0 and 1.
        #
        # Using X and Y instead is not merely rotated: it puts most of the hull
        # outside the projection, so a CLAMP_TO_EDGE axis smears one row of the
        # mask along the whole ship. That is what a wrong axis pair looks like.
        for axis in (1, 2):
            mode = wrap_parts.outputs[axis - 1]
            row = base - (axis - 1) * 140

            # value = raw * (0.5 - flip) + 0.5
            multiplier = nodes.new("ShaderNodeMath")
            multiplier.operation = "SUBTRACT"
            multiplier.location = (100, row - 560)
            multiplier.inputs[0].default_value = 0.5
            links.new(flip_parts.outputs[axis - 1], multiplier.inputs[1])

            mapped = nodes.new("ShaderNodeMath")
            mapped.operation = "MULTIPLY_ADD"
            mapped.location = (160, row + 200)
            mapped.label = f"[-1,1] -> [0,1] {'uv'[axis - 1]}"
            links.new(uv_parts.outputs[axis], mapped.inputs[0])
            links.new(multiplier.outputs[0], mapped.inputs[1])
            mapped.inputs[2].default_value = 0.5
            value = mapped.outputs[0]

            # REPEAT tiles; both clamping modes sample the edge. So the lookup
            # is two cases, chosen by whether the mode is REPEAT.
            fract = nodes.new("ShaderNodeMath")
            fract.operation = "FRACT"
            fract.location = (220, row + 70)
            links.new(value, fract.inputs[0])

            clamped = nodes.new("ShaderNodeClamp")
            clamped.location = (220, row - 70)
            links.new(value, clamped.inputs["Value"])

            is_repeat = nodes.new("ShaderNodeMath")
            is_repeat.operation = "LESS_THAN"
            is_repeat.location = (220, row - 210)
            is_repeat.inputs[1].default_value = 0.5
            is_repeat.label = "mode == REPEAT"
            links.new(mode, is_repeat.inputs[0])

            pick = nodes.new("ShaderNodeMix")
            pick.data_type = "FLOAT"
            pick.location = (380, row)
            links.new(is_repeat.outputs[0], pick.inputs["Factor"])
            links.new(clamped.outputs[0], pick.inputs[2])
            links.new(fract.outputs[0], pick.inputs[3])
            wrapped.append(pick.outputs[0])

            # Only BORDER can cover nothing, and only outside [0, 1].
            is_border = nodes.new("ShaderNodeMath")
            is_border.operation = "GREATER_THAN"
            is_border.location = (380, row - 210)
            is_border.inputs[1].default_value = 1.5
            is_border.label = "mode == BORDER"
            links.new(mode, is_border.inputs[0])

            above = nodes.new("ShaderNodeMath")
            above.operation = "GREATER_THAN"
            above.location = (380, row - 350)
            above.inputs[1].default_value = 0.0
            links.new(value, above.inputs[0])

            below = nodes.new("ShaderNodeMath")
            below.operation = "LESS_THAN"
            below.location = (380, row - 490)
            below.inputs[1].default_value = 1.0
            links.new(value, below.inputs[0])

            inside = nodes.new("ShaderNodeMath")
            inside.operation = "MULTIPLY"
            inside.location = (540, row - 420)
            links.new(above.outputs[0], inside.inputs[0])
            links.new(below.outputs[0], inside.inputs[1])

            # covered = 1 - isBorder * (1 - inside)
            missing = nodes.new("ShaderNodeMath")
            missing.operation = "SUBTRACT"
            missing.location = (700, row - 420)
            missing.inputs[0].default_value = 1.0
            links.new(inside.outputs[0], missing.inputs[1])

            lost = nodes.new("ShaderNodeMath")
            lost.operation = "MULTIPLY"
            lost.location = (700, row - 280)
            links.new(is_border.outputs[0], lost.inputs[0])
            links.new(missing.outputs[0], lost.inputs[1])

            covered = nodes.new("ShaderNodeMath")
            covered.operation = "SUBTRACT"
            covered.location = (760, row - 140)
            covered.inputs[0].default_value = 1.0
            links.new(lost.outputs[0], covered.inputs[1])

            if coverage is None:
                coverage = covered.outputs[0]
            else:
                both = nodes.new("ShaderNodeMath")
                both.operation = "MULTIPLY"
                both.location = (820, row)
                links.new(coverage, both.inputs[0])
                links.new(covered.outputs[0], both.inputs[1])
                coverage = both.outputs[0]

        uv = nodes.new("ShaderNodeCombineXYZ")
        uv.location = (560, base)
        links.new(wrapped[0], uv.inputs["X"])
        links.new(wrapped[1], uv.inputs["Y"])

        links.new(uv.outputs[0], output.inputs[f"UV {index + 1}"])
        links.new(coverage, output.inputs[f"Coverage {index + 1}"])

    return tree


#: The sails detail lookup's own group. Named separately from the pattern one
#: because it is a different space, and reuses none of it.
SAILS_GROUP = "Carbon Sails Projection"


def build_sails_group() -> bpy.types.ShaderNodeTree:
    """Builds the sails detail-texture transform.

    `quadsailsv5` does NOT project: it scales and rotates the mesh's own UV0 and
    looks the detail texture up with that. So there is no position, no
    quaternion and no wrap mode -- only a tiling factor and an angle, both from
    `SailsDetailData`::

        uv' = rotate(uv * data.x, data.y)

    A separate group for the same reason the pattern projection is one: the
    result has to reach an Image Texture node whose colour then feeds the
    material, and routing that through the quad group would be a cycle.

    The scale-then-rotate order matches Blender's Mapping node, which applies
    scale, then rotation, then location.
    """

    existing = bpy.data.node_groups.get(SAILS_GROUP)
    if existing is not None:
        return existing

    tree = _new_group(SAILS_GROUP)
    nodes, links = tree.nodes, tree.links

    tiling = tree.interface.new_socket(name="Tiling", in_out="INPUT", socket_type="NodeSocketFloat")
    tiling.default_value = 1.0
    tiling.description = "SailsDetailData.x -- how many times the sail texture repeats"
    rotation = tree.interface.new_socket(name="Rotation", in_out="INPUT", socket_type="NodeSocketFloat")
    rotation.description = (
        "SailsDetailData.y, in radians. The two sail areas of one hull differ "
        "only in this, so the same texture serves perpendicular surfaces"
    )
    tree.interface.new_socket(name="UV", in_out="OUTPUT", socket_type="NodeSocketVector")

    group_in = nodes.new("NodeGroupInput")
    group_in.location = (-600, 0)
    group_out = nodes.new("NodeGroupOutput")
    group_out.location = (300, 0)

    coordinate = nodes.new("ShaderNodeTexCoord")
    coordinate.location = (-600, 220)

    scale = nodes.new("ShaderNodeCombineXYZ")
    scale.location = (-380, -120)
    scale.inputs["Z"].default_value = 1.0
    links.new(group_in.outputs["Tiling"], scale.inputs["X"])
    links.new(group_in.outputs["Tiling"], scale.inputs["Y"])

    angle = nodes.new("ShaderNodeCombineXYZ")
    angle.location = (-380, -260)
    links.new(group_in.outputs["Rotation"], angle.inputs["Z"])

    mapping = nodes.new("ShaderNodeMapping")
    mapping.vector_type = "POINT"
    mapping.location = (-140, 0)
    mapping.label = "uv * tiling, rotated"
    links.new(coordinate.outputs["UV"], mapping.inputs["Vector"])
    links.new(scale.outputs[0], mapping.inputs["Scale"])
    links.new(angle.outputs[0], mapping.inputs["Rotation"])

    links.new(mapping.outputs["Vector"], group_out.inputs["UV"])
    return tree


def build_group(member: Optional[Member] = None, *, rebuild: bool = False):
    """The node group for one family member, built once and then shared.

    Reuse is not an optimisation, it is correctness. A hull can have SEVERAL
    areas on one member -- a Legion has two `quadsailsv5` areas -- and rebuilding
    on the second call removes the group the first material is using, leaving
    that material with no node tree and an unlinked surface. It renders as
    nothing, which looks like a missing area rather than a destroyed group.

    Pass `rebuild=True` to force a fresh graph after changing this module.
    """

    member = member or load_family().member("quadv5.fx")
    name = f"{GROUP_PREFIX} {member.name}"
    if not rebuild:
        existing = bpy.data.node_groups.get(name)
        if existing is not None:
            return existing
    tree = _new_group(name)
    nodes, links = tree.nodes, tree.links

    for name, kind, description in OUTPUTS:
        socket = tree.interface.new_socket(name=name, in_out="OUTPUT", socket_type=kind)
        socket.description = description

    panels: dict = {}

    # --- Texture inputs, in the order the shader binds them -----------------
    textures = _panel(tree, "Textures", panels)
    for texture in member.textures:
        annotation = member.annotation(texture)
        note = annotation.description or f"{texture}"
        if annotation.srgb:
            note += "  (sRGB)"
        if annotation.uv_scale != 1.0:
            note += f"  (UV x{annotation.uv_scale:g})"
        _socket(tree, texture, "NodeSocketColor", description=note,
                default=(0.0, 0.0, 0.0, 1.0), panel=textures)
    if "DustNoiseMap" in member.textures:
        _socket(tree, DUST_ALPHA, "NodeSocketFloat", panel=textures,
                description="Dust noise alpha; drives the dirt mask. "
                            "The RGB channels drive the dusty albedo, F0 and roughness")

    # Pattern coverage sockets are added AFTER the constants below, so the
    # Pattern Material panels are created after the Material ones and therefore
    # sit under them. Panels appear in creation order, so touching one early
    # would float it to the top.

    # --- Constants, grouped and defaulted exactly as Carbon declares them ---
    for name, constant in member.constants.items():
        annotation = member.annotation(name)
        panel = _panel(tree, annotation.group or "Other", panels)
        exposed = socket_name(name)
        note = annotation.description or annotation.component(1)
        if exposed != name:
            note = f"{note}  (Carbon calls this {name}.{'xyzw'[0]})" if note else f"Carbon's {name}.x"
        if annotation.is_color:
            _socket(tree, exposed, "NodeSocketColor", description=note,
                    default=tuple(constant.default[:3]) + (1.0,), panel=panel)
            continue

        # A vec4 of four separate quantities gets a socket per lane, named from
        # Carbon's own annotations -- MtlNHeatGlowData is boosterGain
        # influence, Shimmer speed, Shimmer size and Shimmer strength, and
        # exposing only .x silently drops three of them. A renamed constant is
        # excluded: renaming is the statement that one lane is the meaningful
        # one, which is why GeneralData stays a single PaintMaskInfluence.
        lanes = [] if exposed != name else annotation.components()
        if len(lanes) > 1:
            for index, lane in enumerate(lanes):
                _socket(tree, f"{name.replace('Data', '')} {lane}", "NodeSocketFloat",
                        description=f"{name}.{'xyzw'[index]}", panel=panel,
                        default=float(constant.default[index]))
            continue

        _socket(tree, exposed, "NodeSocketFloat", description=note,
                default=float(constant.default[0]), panel=panel)

    # Now that the Material panels exist, the Pattern Material ones land under
    # them. Coverage comes from the projection group rather than from a texture:
    # only a CLAMP_TO_BORDER axis can leave a texel uncovered, and that is
    # decided by the projected coordinate, not by the mask image.
    for index in (1, 2):
        if f"PatternMask{index}Map" in member.textures:
            _socket(tree, f"Pattern{index}Coverage", "NodeSocketFloat",
                    panel=_panel(tree, PATTERN_PANELS[index - 1], panels),
                    default=1.0, min_value=0.0, max_value=1.0,
                    description=f"From the {PROJECTION_GROUP} group's Coverage {index}; "
                                "zero outside a bordered projection")

    group_in = nodes.new("NodeGroupInput")
    group_in.location = (-1400, 0)
    group_out = nodes.new("NodeGroupOutput")
    group_out.location = (1200, 0)

    ship = {}

    def has(name):
        return name in group_in.outputs or name in SHIP_PROPERTIES

    def value(name):
        if name in group_in.outputs:
            return group_in.outputs[name]
        if name not in SHIP_PROPERTIES:
            return None
        # A SOCKET, not an Attribute node. EEVEE delivers only EIGHT object
        # attributes per material and silently returns zero for the rest, with
        # no error and a valid-looking render. The two pattern masks already
        # spend sixteen, so a ship value read through an attribute lands
        # outside the eight that arrive and reads zero -- which is what turned
        # the boosters black: activation and emission strength both went to
        # zero and took the whole emissive term with them.
        #
        # `drive_ship_values` puts a driver on this socket instead, so the
        # value still comes from the OBJECT and a hull and its decals still
        # agree, at no attribute cost.
        if name not in ship:
            prop, default = SHIP_PROPERTIES[name]
            socket = tree.interface.new_socket(
                name=name, in_out="INPUT", socket_type="NodeSocketFloat")
            socket.default_value = default
            socket.description = (
                f"Per-ship value, driven from the object's {prop} property"
            )
            ship[name] = group_in.outputs[name]
        return ship[name]

    def math(op, a, b=None, *, location=(0, 0), clamp=False, label=""):
        node = nodes.new("ShaderNodeMath")
        node.operation = op
        node.use_clamp = clamp
        node.location = location
        node.label = label
        for index, operand in enumerate((a, b)):
            if operand is None:
                continue
            if isinstance(operand, (int, float)):
                node.inputs[index].default_value = operand
            else:
                links.new(operand, node.inputs[index])
        return node.outputs[0]

    def vector(op, a, b=None, *, location=(0, 0), label=""):
        node = nodes.new("ShaderNodeVectorMath")
        node.operation = op
        node.location = location
        node.label = label
        for index, operand in enumerate((a, b)):
            if operand is None:
                continue
            if isinstance(operand, (int, float)):
                node.inputs[index].default_value = (operand,) * 3
            elif isinstance(operand, tuple):
                node.inputs[index].default_value = operand
            else:
                links.new(operand, node.inputs[index])
        return node.outputs[0]

    def separate(source, location=(0, 0)):
        node = nodes.new("ShaderNodeSeparateColor")
        node.location = location
        links.new(source, node.inputs[0])
        return node.outputs

    # --- Material weights: the tent filter ----------------------------------
    # clamp(OFFSET - abs((MaterialMap.x - centre) * SLOPE), 0, 1)
    material_x = separate(value("MaterialMap"), (-1200, 400))[0]

    # `quadsailsv5` re-selects which material layer is used, rather than adding
    # one: the sail texture is blended over the MaterialMap selector by the tent
    # weight of LAYER 1, so the pattern only acts where the first material is
    # chosen -- which is what makes that region the sail.
    if has("SailsDetailMap"):
        scaled = math("MULTIPLY", material_x, reference.MATERIAL_TENT_SLOPE,
                      location=(-1140, 300))
        absolute = math("ABSOLUTE", scaled, location=(-1080, 300))
        layer1 = math("SUBTRACT", reference.MATERIAL_TENT_OFFSET, absolute,
                      location=(-1020, 300), clamp=True, label="Mtl1 weight")
        sails = separate(value("SailsDetailMap"), (-1200, 220))[0]
        difference = math("SUBTRACT", sails, material_x, location=(-960, 240))
        weighted = math("MULTIPLY", difference, layer1, location=(-920, 240))
        material_x = math("ADD", weighted, material_x, location=(-880, 300),
                          label="sails re-selects")

    weights = []
    for layer, centre in enumerate(reference.MATERIAL_TENT_CENTRES):
        y = 600 - layer * 160
        shifted = math("SUBTRACT", material_x, centre, location=(-1000, y))
        scaled = math("MULTIPLY", shifted, reference.MATERIAL_TENT_SLOPE, location=(-840, y))
        absolute = math("ABSOLUTE", scaled, location=(-680, y))
        weights.append(math("SUBTRACT", reference.MATERIAL_TENT_OFFSET, absolute,
                            location=(-520, y), clamp=True,
                            label=f"Mtl{layer + 1} weight"))

    def toward(source, target, factor, location, label=""):
        node = nodes.new("ShaderNodeMix")
        node.data_type = "RGBA"
        node.blend_type = "MIX"
        node.location = location
        node.label = label
        links.new(factor, node.inputs["Factor"])
        links.new(source, node.inputs[6])
        if isinstance(target, tuple):
            node.inputs[7].default_value = target
        else:
            links.new(target, node.inputs[7])
        return node.outputs[2]

    # --- Pattern coverage, one effective mask per projection ----------------
    #
    # A pattern does not tint the finished colour: it REPLACES a base material
    # layer where its mask covers, per layer, gated by that projection's
    # targetMaterials. Measured from the pixel stage, which for each layer n
    # does `mix(Mtl_n, patternMaterial, maskSample * target[n])`, mask 1 then
    # mask 2 -- which is what "pattern 2 on top of pattern 1" means.
    patterns = []
    for index in (1, 2):
        mask_name = f"PatternMask{index}Map"
        if not has(mask_name):
            continue
        sample = separate(value(mask_name), (-1200, 900 - index * 120))[0]
        coverage_name = f"Pattern{index}Coverage"
        if has(coverage_name):
            sample = math("MULTIPLY", sample, value(coverage_name),
                          location=(-1040, 900 - index * 120),
                          label=f"mask {index} x coverage")

        # ccpwgl binds mask 0 to PMtl1 and mask 1 to PMtl2; materialIndex can in
        # principle name a base material too, which is not implemented.
        prefix = f"PMtl{index}"
        targets = nodes.new("ShaderNodeAttribute")
        targets.attribute_type = "OBJECT"
        targets.attribute_name = MASK_PROPERTIES["targets"].format(index - 1)
        targets.location = (-1200, 700 - index * 120)
        targets.label = targets.attribute_name
        target_rgb = separate(targets.outputs["Color"], (-1040, 700 - index * 120))

        target4 = nodes.new("ShaderNodeAttribute")
        target4.attribute_type = "OBJECT"
        target4.attribute_name = MASK_PROPERTIES["target4"].format(index - 1)
        target4.location = (-1200, 640 - index * 120)
        target4.label = target4.attribute_name

        per_layer = [target_rgb[0], target_rgb[1], target_rgb[2], target4.outputs["Factor"]]
        patterns.append((prefix, sample, per_layer))

    def patterned(layer, socket, suffix, location, *, scalar=False):
        """One layer's constant after every projection has had its say."""

        for order, (prefix, sample, targets) in enumerate(patterns):
            source = f"{prefix}{suffix}"
            if not has(source):
                continue
            factor = math("MULTIPLY", sample, targets[layer],
                          location=(location[0] - 120, location[1] - order * 40))
            if scalar:
                node = nodes.new("ShaderNodeMix")
                node.data_type = "FLOAT"
                node.location = (location[0], location[1] - order * 40)
                links.new(factor, node.inputs["Factor"])
                links.new(socket, node.inputs[2])
                links.new(value(source), node.inputs[3])
                socket = node.outputs[0]
            else:
                socket = toward(socket, value(source), factor,
                                (location[0], location[1] - order * 40),
                                f"{prefix} over Mtl{layer + 1}")
        return socket

    def blend4(prefix, suffix, location_y, *, scalar=False):
        """Weighted sum of the four per-layer constants, patterns applied first.

        Order matters: a pattern replaces a LAYER, so it has to happen before
        the four layers are summed. Applying it to the summed result would tint
        regions the projection never targeted.
        """

        total = None
        for layer, weight in enumerate(weights):
            name = f"{prefix}{layer + 1}{suffix}"
            if not has(name):
                return None
            x = -340 + layer * 40
            row = location_y - layer * 30
            source = value(name)
            if prefix == "Mtl" and patterns:
                source = patterned(layer, source, suffix, (x - 260, row), scalar=scalar)
            if scalar:
                term = math("MULTIPLY", source, weight, location=(x, row))
                total = term if total is None else math("ADD", total, term,
                                                        location=(x + 20, row))
            else:
                term = vector("SCALE", source, None, location=(x, row))
                links.new(weight, term.node.inputs["Scale"])
                total = term if total is None else vector("ADD", total, term,
                                                          location=(x + 20, row))
        return total

    diffuse = blend4("Mtl", "DiffuseColor", 600)
    fresnel = blend4("Mtl", "FresnelColor", 300)
    gloss = blend4("Mtl", "Gloss", 100, scalar=True)
    dust = blend4("Mtl", "DustDiffuseColor", -100)

    # --- Paint mask ---------------------------------------------------------
    paint = None
    if has("PaintMaskMap"):
        paint_x = separate(value("PaintMaskMap"), (-1200, 200))[0]
        influence_socket = socket_name("GeneralData")
        influence = value(influence_socket) if has(influence_socket) else None
        paint = math("MULTIPLY", paint_x, influence if influence else 1.0,
                     location=(-1000, 200), label="paint strength")

    if paint is not None and diffuse is not None:
        diffuse = toward(diffuse, (1.0, 1.0, 1.0, 1.0), paint, (-140, 600), "paint -> white")
    if paint is not None and fresnel is not None:
        fresnel = toward(fresnel, tuple(reference.PAINT_FRESNEL_COLOR) + (1.0,),
                         paint, (-140, 300), "paint -> dielectric")

    # --- The dust noise's three colour channels, each biased by +0.5 --------
    # x drives the dusty albedo, y its F0, z its roughness, and the alpha the
    # mask. Each channel is used separately, which is why the bias matters on
    # all four.
    noise = [None, None, None]
    if has("DustNoiseMap"):
        channels = separate(value("DustNoiseMap"), (-1200, -160))
        for index in range(3):
            noise[index] = math("ADD", channels[index], reference.DUST_BIAS,
                                location=(-1000, -120 - index * 70),
                                label=f"noise.{'xyz'[index]} +0.5")

    # --- Albedo, clean and dusty --------------------------------------------
    albedo_map = value("AlbedoMap")
    clean = vector("MULTIPLY", diffuse, albedo_map, location=(60, 600), label="clean albedo")
    dusty = None
    if dust is not None:
        dusty = vector("MULTIPLY", dust, albedo_map, location=(60, -100), label="dusty albedo")
        if noise[0] is not None:
            scaled = vector("SCALE", dusty, None, location=(200, -100), label="* noise.x")
            links.new(noise[0], scaled.node.inputs["Scale"])
            dusty = scaled

    # --- Roughness: (1 - gloss * roughnessMap, paint -> 0.4) squared ---------
    roughness = None
    dusty_roughness = None
    roughness_x = separate(value("RoughnessMap"), (-1200, 0))[0] if has("RoughnessMap") else None
    if gloss is not None and roughness_x is not None:
        combined = math("MULTIPLY", gloss, roughness_x, location=(-140, 100))
        if paint is not None:
            node = nodes.new("ShaderNodeMix")
            node.data_type = "FLOAT"
            node.location = (20, 100)
            node.label = "paint -> flat 0.4"
            links.new(paint, node.inputs["Factor"])
            links.new(combined, node.inputs[2])
            node.inputs[3].default_value = reference.PAINT_GLOSS
            combined = node.outputs[0]
        linear = math("SUBTRACT", 1.0, combined, location=(180, 100), clamp=True)
        roughness = math("MULTIPLY", linear, linear, location=(340, 100), label="roughness")

    # The dusty side uses the BAKED dirt gloss, not the blended material gloss,
    # and the paint mask does not enter it: dirt sits on top of paint.
    if roughness_x is not None and noise[2] is not None:
        dulled = math("MULTIPLY", roughness_x, noise[2], location=(-140, -300))
        dulled = math("MULTIPLY", dulled, reference.DIRT_GLOSS, location=(20, -300))
        dust_linear = math("SUBTRACT", 1.0, dulled, location=(180, -300), clamp=True)
        dusty_roughness = math("MULTIPLY", dust_linear, dust_linear, location=(340, -300),
                               label="dusty roughness")

    # --- Dusty F0 is its own baked colour, not the material's ---------------
    dusty_fresnel = None
    if noise[1] is not None:
        node = nodes.new("ShaderNodeVectorMath")
        node.operation = "SCALE"
        node.location = (-140, -500)
        node.label = "dirt F0"
        node.inputs[0].default_value = reference.DIRT_FRESNEL_COLOR
        links.new(noise[1], node.inputs["Scale"])
        dusty_fresnel = node.outputs[0]

    # --- Dirt mask, then blend every surface parameter by it ----------------
    albedo = clean
    if has("DirtMap") and has(DUST_ALPHA):
        dirt_x = separate(value("DirtMap"), (-1200, -700))[0]
        biased = math("ADD", value(DUST_ALPHA), reference.DUST_BIAS, location=(-1000, -760),
                      label="noise.w +0.5")
        masked = math("MULTIPLY", dirt_x, biased, location=(-840, -720))
        # Carbon's dirt level from weeks since cleaned:
        #   max(0.7 - 1 / (max(weeks, 0) ** 0.65 + 1 / 2.7), 0)
        # It is negative below about a week, so a fresh hull is clean, and it
        # saturates toward 0.7.
        aged = math("POWER", math("MAXIMUM", value("AgeInWeeks"), 0.0, location=(-1200, -900)),
                    reference.DIRT_AGE_EXPONENT, location=(-1060, -900))
        shifted = math("ADD", aged, reference.DIRT_AGE_BIAS, location=(-1000, -940))
        falling = math("DIVIDE", 1.0, shifted, location=(-940, -900))
        level = math("SUBTRACT", reference.DIRT_AGE_CEILING, falling, location=(-900, -940))
        level = math("MAXIMUM", level, 0.0, location=(-870, -900), label="dirt level")
        divisor = math("SUBTRACT", 1.0, level, location=(-840, -820))
        mask = math("DIVIDE", masked, divisor, location=(-680, -760), clamp=True,
                    label="dirt mask")

        # Production weights the CLEAN side by (1 - mask)^3 and the dusty side
        # by mask, then blends two lit results. Lighting once, the balance is
        # carried across as the dusty share of the total, and the total itself
        # is applied to the albedo as a dimming.
        #
        # Using the raw mask as a mix factor instead -- the obvious thing --
        # makes dirt far too weak: at a mask of 0.5 the surface should be 80%
        # dusty, not 50%.
        inverse = math("SUBTRACT", 1.0, mask, location=(-620, -820))
        cubed = math("POWER", inverse, 3.0, location=(-560, -820), label="(1-mask)^3")
        total = math("ADD", cubed, mask, location=(-500, -790), label="dirt energy")
        factor = math("DIVIDE", mask, total, location=(-440, -760), clamp=True,
                      label="dusty share")

        if dusty is not None:
            albedo = toward(clean, dusty, factor, (500, 300), "clean -> dusty")
            # The authored weights sum to less than one across the mid-range, so
            # a half-dirty texel really is darker than either side.
            dimmed = vector("SCALE", albedo, None, location=(620, 300), label="* dirt energy")
            links.new(total, dimmed.node.inputs["Scale"])
            albedo = dimmed
        if fresnel is not None and dusty_fresnel is not None:
            fresnel = toward(fresnel, dusty_fresnel, factor, (500, 0), "F0 clean -> dusty")
        if roughness is not None and dusty_roughness is not None:
            node = nodes.new("ShaderNodeMix")
            node.data_type = "FLOAT"
            node.location = (500, -300)
            node.label = "roughness clean -> dusty"
            links.new(factor, node.inputs["Factor"])
            links.new(roughness, node.inputs[2])
            links.new(dusty_roughness, node.inputs[3])
            roughness = node.outputs[0]

    # --- Normal: two channels, +0.002, and an implicit Z of 1 ---------------
    # Blender's Normal Map node computes normalize(T*(2r-1) + B*(2g-1) + N*(2b-1)).
    # Carbon computes normalize(T*x + B*y + N*1). Feeding blue = 1.0 makes the
    # third term exactly 1.0, so the two agree without a reconstructed Z.
    normal = None
    if has("NormalMap"):
        channels = separate(value("NormalMap"), (-1200, -600))
        red = math("ADD", channels[0], reference.NORMAL_BIAS, location=(-1000, -560))
        green = math("ADD", channels[1], reference.NORMAL_BIAS, location=(-1000, -640))
        combine = nodes.new("ShaderNodeCombineColor")
        combine.location = (-840, -600)
        combine.inputs[2].default_value = 1.0
        links.new(red, combine.inputs[0])
        links.new(green, combine.inputs[1])
        normal_node = nodes.new("ShaderNodeNormalMap")
        normal_node.location = (-680, -600)
        normal_node.label = "implicit Z = 1"
        links.new(combine.outputs[0], normal_node.inputs["Color"])
        normal = normal_node.outputs[0]

    # --- Emission: pow(GlowMap.x, 2.4) * colour * activation ----------------
    # --- Heat: a gate on booster gain, scaling the glow ---------------------
    #
    # quadheatv5 does not add a texture of its own: it scales the GLOW map by a
    # gate on the object's booster gain, so a hull with no glow detail shows no
    # heat however hot it is. The gate window is tiny -- subtract 0.005,
    # multiply by 66.667 -- so heat is fully on by a gain of 0.02.
    #
    # `boosterGain influence` is Carbon's own name for the lane, and a material
    # whose influence is zero ignores the boosters and always glows.
    heat_amount = None
    if has("BoosterGain") and has("Mtl1HeatGlow boosterGain influence"):
        influence = blend4("Mtl", "HeatGlow boosterGain influence", -700, scalar=True)
        if influence is not None:
            shifted = math("SUBTRACT", value("BoosterGain"), reference.HEAT_GATE_START,
                           location=(-1000, -700))
            gate = math("MULTIPLY", shifted, reference.HEAT_GATE_SCALE,
                        location=(-940, -700), clamp=True, label="booster gate")
            below = math("SUBTRACT", gate, 1.0, location=(-880, -700))
            scaled = math("MULTIPLY", influence, below, location=(-820, -700))
            heat_amount = math("ADD", scaled, 1.0, location=(-760, -700),
                               clamp=True, label="heat amount")

    emission = None
    glow_color = "GeneralGlowColor" if has("GeneralGlowColor") else "GeneralHeatGlowColor"
    if has("GlowMap") and has(glow_color):
        glow_x = separate(value("GlowMap"), (-1200, -900))[0]
        powered = math("POWER", glow_x, reference.GLOW_INNER_EXPONENT * reference.GLOW_OUTER_EXPONENT,
                       location=(-1000, -900), label="glow ^2.4")
        scaled = math("MULTIPLY", powered, value("Activation"), location=(-840, -900))
        if heat_amount is not None:
            scaled = math("MULTIPLY", scaled, heat_amount, location=(-800, -930),
                          label="gated by boosters")
        scaled = math("MULTIPLY", scaled, value("EmissionStrength"), location=(-760, -960))
        emission = vector("SCALE", value(glow_color), None, location=(-680, -900))
        links.new(scaled, emission.node.inputs["Scale"])

    # --- Surface ------------------------------------------------------------
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (800, 0)
    principled.inputs["Metallic"].default_value = 0.0
    if albedo is not None:
        links.new(albedo, principled.inputs["Base Color"])
        links.new(albedo, group_out.inputs["Albedo"])
    if roughness is not None:
        links.new(roughness, principled.inputs["Roughness"])
        links.new(roughness, group_out.inputs["Roughness"])
    if fresnel is not None:
        # Carbon's fresnel colour IS F0. Principled expresses a dielectric's F0
        # as `0.08 * Specular IOR Level * Specular Tint`, so the colour is
        # scaled by 1/0.08 at full level to land F0 = colour exactly.
        #
        # Feeding the colour straight in instead makes everything 12.5 times
        # less reflective. That is survivable for the material layers, which are
        # authored HDR and often around 3, but not for the BAKED constants: the
        # dirt F0 of (0.019, 0.017, 0.014) and the paint dielectric are literal
        # F0 values in the usual 0..1 range, and scaling them down leaves dirt
        # barely reflective at all -- which is what it looked like.
        #
        # Nothing here clamps: Blender's sockets hold values above 1 and
        # preserve them through a .blend round-trip (verified). The one place
        # they are silently destroyed is the colour PICKER widget, which clamps
        # what a user drags even though typed values persist.
        principled.inputs["Specular IOR Level"].default_value = 1.0
        f0 = vector("SCALE", fresnel, None, location=(660, 0), label="F0 -> Specular Tint")
        f0.node.inputs["Scale"].default_value = 1.0 / 0.08
        links.new(f0, principled.inputs["Specular Tint"])
        links.new(fresnel, group_out.inputs["Fresnel"])
    if normal is not None:
        links.new(normal, principled.inputs["Normal"])
        links.new(normal, group_out.inputs["Normal"])
    if emission is not None:
        links.new(emission, principled.inputs["Emission Color"])
        links.new(emission, group_out.inputs["Emission"])
        principled.inputs["Emission Strength"].default_value = 1.0

    links.new(principled.outputs["BSDF"], group_out.inputs["BSDF"])
    return tree


def build_all() -> list:
    """Builds a group for every measured family member."""

    family = load_family()
    return [build_group(member) for member in family.members.values()]


#: The decal projection group; one per blend file, shared by every decal.
DECAL_PROJECTION_GROUP = "Carbon Decal Projection"


def build_decal_projection_group() -> bpy.types.ShaderNodeTree:
    """Builds the decal projection, read from the decal object's own properties.

    The same convention as the quad patterns -- rows 1 and 2 of the inverse
    matrix over a `[-1, 1]` box -- so this is the pattern group with one
    projection instead of two, and no wrap modes: every decal map clamps to a
    black border, which Blender's CLIP extension does natively.

    A decal's transform belongs to the decal, not the ship, so the properties
    are read from the decal object being shaded. One group therefore serves
    every decal on every hull.
    """

    existing = bpy.data.node_groups.get(DECAL_PROJECTION_GROUP)
    if existing is not None:
        return existing

    tree = _new_group(DECAL_PROJECTION_GROUP)
    nodes, links = tree.nodes, tree.links
    tree.interface.new_socket(name="UV", in_out="OUTPUT", socket_type="NodeSocketVector")

    output = nodes.new("NodeGroupOutput")
    output.location = (400, 0)
    coordinate = nodes.new("ShaderNodeTexCoord")
    coordinate.location = (-700, 0)

    def attribute(name, row):
        node = nodes.new("ShaderNodeAttribute")
        node.attribute_type = "OBJECT"
        node.attribute_name = name
        node.location = (-700, row)
        node.label = name
        return node.outputs["Vector"]

    mapping = nodes.new("ShaderNodeMapping")
    mapping.vector_type = "TEXTURE"
    mapping.location = (-400, 0)
    mapping.label = "inverse decal transform"
    links.new(coordinate.outputs["Object"], mapping.inputs["Vector"])
    links.new(attribute("carbon_decal_position", 240), mapping.inputs["Location"])
    links.new(attribute("carbon_decal_rotation", 120), mapping.inputs["Rotation"])
    links.new(attribute("carbon_decal_scaling", -60), mapping.inputs["Scale"])

    parts = nodes.new("ShaderNodeSeparateXYZ")
    parts.location = (-160, 0)
    links.new(mapping.outputs["Vector"], parts.inputs[0])

    # Rows 1 and 2, and V flipped for D3D texture space, exactly as the
    # patterns need.
    uv = nodes.new("ShaderNodeCombineXYZ")
    uv.location = (200, 0)
    uv.label = "[-1,1] -> [0,1], V flipped"

    for axis, socket, sign in ((1, "X", 0.5), (2, "Y", -0.5)):
        mapped = nodes.new("ShaderNodeMath")
        mapped.operation = "MULTIPLY_ADD"
        mapped.location = (20, -axis * 140)
        links.new(parts.outputs[axis], mapped.inputs[0])
        mapped.inputs[1].default_value = sign
        mapped.inputs[2].default_value = 0.5
        links.new(mapped.outputs[0], uv.inputs[socket])

    links.new(uv.outputs[0], output.inputs["UV"])
    return tree


#: The two groups the heat shimmer needs, and why there are two.
#:
#: The shimmer displaces the GLOW lookup by a product of two noise taps, so the
#: chain is: compute noise UVs, sample the noise, compute a displacement, sample
#: the glow. Each sample has to happen in the material, between groups, because
#: a group cannot feed a texture that feeds itself back.
HEAT_UV_GROUP = "Carbon Heat Noise UV"
HEAT_DISPLACE_GROUP = "Carbon Heat Displace"


def _tent_weights(tree, material_socket, x=-600):
    """The four material weights, inside a helper group.

    The same tent as the quad group's, rebuilt here because the heat groups run
    before it in the chain and cannot reach its weights without a cycle.
    """

    nodes, links = tree.nodes, tree.links
    weights = []
    for layer, centre in enumerate(reference.MATERIAL_TENT_CENTRES):
        row = 300 - layer * 120
        shifted = nodes.new("ShaderNodeMath")
        shifted.operation = "SUBTRACT"
        shifted.location = (x, row)
        links.new(material_socket, shifted.inputs[0])
        shifted.inputs[1].default_value = centre

        scaled = nodes.new("ShaderNodeMath")
        scaled.operation = "MULTIPLY"
        scaled.location = (x + 160, row)
        links.new(shifted.outputs[0], scaled.inputs[0])
        scaled.inputs[1].default_value = reference.MATERIAL_TENT_SLOPE

        absolute = nodes.new("ShaderNodeMath")
        absolute.operation = "ABSOLUTE"
        absolute.location = (x + 300, row)
        links.new(scaled.outputs[0], absolute.inputs[0])

        weight = nodes.new("ShaderNodeMath")
        weight.operation = "SUBTRACT"
        weight.location = (x + 440, row)
        weight.use_clamp = True
        weight.label = f"Mtl{layer + 1} weight"
        weight.inputs[0].default_value = reference.MATERIAL_TENT_OFFSET
        links.new(absolute.outputs[0], weight.inputs[1])
        weights.append(weight.outputs[0])
    return weights


def _blend_lanes(tree, weights, sockets, x, y):
    """Weighted sum of four per-layer scalars."""

    nodes, links = tree.nodes, tree.links
    total = None
    for layer, (weight, socket) in enumerate(zip(weights, sockets)):
        term = nodes.new("ShaderNodeMath")
        term.operation = "MULTIPLY"
        term.location = (x, y - layer * 60)
        links.new(socket, term.inputs[0])
        links.new(weight, term.inputs[1])
        if total is None:
            total = term.outputs[0]
            continue
        add = nodes.new("ShaderNodeMath")
        add.operation = "ADD"
        add.location = (x + 140, y - layer * 60)
        links.new(total, add.inputs[0])
        links.new(term.outputs[0], add.inputs[1])
        total = add.outputs[0]
    return total


def _time_value(tree, location=(-900, -400)):
    """A Value node driven by the scene clock, in seconds.

    Shader trees have no clock, so the shimmer would otherwise be frozen. A
    driver on `frame / fps` gives the same seconds Carbon passes as Time, and
    evaluates through the depsgraph like any other driver.
    """

    node = tree.nodes.new("ShaderNodeValue")
    node.location = location
    node.label = "Time (seconds)"
    curve = node.outputs[0].driver_add("default_value")
    curve.driver.type = "SCRIPTED"
    curve.driver.expression = "frame / fps"
    variable = curve.driver.variables.new()
    variable.name = "fps"
    variable.type = "SINGLE_PROP"
    variable.targets[0].id_type = "SCENE"
    variable.targets[0].id = bpy.context.scene
    variable.targets[0].data_path = "render.fps"
    return node.outputs[0]


def build_heat_uv_group() -> bpy.types.ShaderNodeTree:
    """The two counter-scrolling noise coordinates.

    `uv' = (uv +/- speed * time) * size`. The taps scroll in OPPOSITE
    directions, which is what stops the shimmer reading as a texture sliding
    past.
    """

    existing = bpy.data.node_groups.get(HEAT_UV_GROUP)
    if existing is not None:
        return existing

    tree = _new_group(HEAT_UV_GROUP)
    nodes, links = tree.nodes, tree.links
    tree.interface.new_socket(name="MaterialMap", in_out="INPUT", socket_type="NodeSocketFloat")
    for layer in range(1, 5):
        for lane in ("Shimmer speed", "Shimmer size"):
            socket = tree.interface.new_socket(
                name=f"Mtl{layer}HeatGlow {lane}", in_out="INPUT", socket_type="NodeSocketFloat")
            socket.default_value = 1.0 if lane == "Shimmer size" else 0.0
    tree.interface.new_socket(name="Noise UV 1", in_out="OUTPUT", socket_type="NodeSocketVector")
    tree.interface.new_socket(name="Noise UV 2", in_out="OUTPUT", socket_type="NodeSocketVector")

    group_in = nodes.new("NodeGroupInput")
    group_in.location = (-1200, 0)
    group_out = nodes.new("NodeGroupOutput")
    group_out.location = (700, 0)

    weights = _tent_weights(tree, group_in.outputs["MaterialMap"], x=-1000)
    speed = _blend_lanes(tree, weights,
                         [group_in.outputs[f"Mtl{n}HeatGlow Shimmer speed"] for n in range(1, 5)],
                         -300, 200)
    size = _blend_lanes(tree, weights,
                        [group_in.outputs[f"Mtl{n}HeatGlow Shimmer size"] for n in range(1, 5)],
                        -300, -100)

    coordinate = nodes.new("ShaderNodeTexCoord")
    coordinate.location = (-1200, -300)
    scroll = nodes.new("ShaderNodeMath")
    scroll.operation = "MULTIPLY"
    scroll.location = (100, -400)
    scroll.label = "speed x time"
    links.new(speed, scroll.inputs[0])
    links.new(_time_value(tree), scroll.inputs[1])

    for index, sign in ((1, 1.0), (2, -1.0)):
        signed = nodes.new("ShaderNodeMath")
        signed.operation = "MULTIPLY"
        signed.location = (250, -300 - index * 120)
        links.new(scroll.outputs[0], signed.inputs[0])
        signed.inputs[1].default_value = sign

        offset = nodes.new("ShaderNodeCombineXYZ")
        offset.location = (380, -300 - index * 120)
        links.new(signed.outputs[0], offset.inputs["X"])
        links.new(signed.outputs[0], offset.inputs["Y"])

        mapping = nodes.new("ShaderNodeMapping")
        mapping.vector_type = "POINT"
        mapping.location = (520, 200 - index * 260)
        mapping.label = f"tap {index}"
        links.new(coordinate.outputs["UV"], mapping.inputs["Vector"])
        links.new(offset.outputs[0], mapping.inputs["Location"])
        scale = nodes.new("ShaderNodeCombineXYZ")
        scale.location = (380, 100 - index * 260)
        scale.inputs["Z"].default_value = 1.0
        links.new(size, scale.inputs["X"])
        links.new(size, scale.inputs["Y"])
        links.new(scale.outputs[0], mapping.inputs["Scale"])
        links.new(mapping.outputs["Vector"], group_out.inputs[f"Noise UV {index}"])

    return tree


def build_heat_displace_group() -> bpy.types.ShaderNodeTree:
    """Turns the two noise samples into the displaced glow coordinate.

    `glowUv = uv + strength * amount * (n1 * n2 - 0.5)`. The product is centred
    on 0.5, so average noise leaves the glow exactly where it is; the shimmer
    only ever pushes it off centre.

    The heat amount is recomputed here rather than passed in, because it comes
    from the object's booster gain and this group runs before the quad group in
    the chain. Both read the same property, so they cannot disagree.
    """

    existing = bpy.data.node_groups.get(HEAT_DISPLACE_GROUP)
    if existing is not None:
        return existing

    tree = _new_group(HEAT_DISPLACE_GROUP)
    nodes, links = tree.nodes, tree.links
    tree.interface.new_socket(name="MaterialMap", in_out="INPUT", socket_type="NodeSocketFloat")
    tree.interface.new_socket(name="Noise 1", in_out="INPUT", socket_type="NodeSocketColor")
    tree.interface.new_socket(name="Noise 2", in_out="INPUT", socket_type="NodeSocketColor")
    for layer in range(1, 5):
        for lane, default in (("Shimmer strength", 0.0), ("boosterGain influence", 1.0)):
            socket = tree.interface.new_socket(
                name=f"Mtl{layer}HeatGlow {lane}", in_out="INPUT", socket_type="NodeSocketFloat")
            socket.default_value = default
    tree.interface.new_socket(name="Glow UV", in_out="OUTPUT", socket_type="NodeSocketVector")

    group_in = nodes.new("NodeGroupInput")
    group_in.location = (-1200, 0)
    group_out = nodes.new("NodeGroupOutput")
    group_out.location = (900, 0)

    weights = _tent_weights(tree, group_in.outputs["MaterialMap"], x=-1000)
    strength = _blend_lanes(tree, weights,
                            [group_in.outputs[f"Mtl{n}HeatGlow Shimmer strength"] for n in range(1, 5)],
                            -300, 200)
    influence = _blend_lanes(tree, weights,
                             [group_in.outputs[f"Mtl{n}HeatGlow boosterGain influence"] for n in range(1, 5)],
                             -300, -100)

    # A socket, driven from the object -- see `drive_ship_values` for why this
    # cannot be an Attribute node.
    booster = tree.interface.new_socket(
        name="BoosterGain", in_out="INPUT", socket_type="NodeSocketFloat")
    booster.default_value = SHIP_PROPERTIES["BoosterGain"][1]
    gain_socket = group_in.outputs["BoosterGain"]

    def math(op, a, b, location, clamp=False, label=""):
        node = nodes.new("ShaderNodeMath")
        node.operation = op
        node.location = location
        node.use_clamp = clamp
        node.label = label
        for index, operand in enumerate((a, b)):
            if isinstance(operand, (int, float)):
                node.inputs[index].default_value = operand
            else:
                links.new(operand, node.inputs[index])
        return node.outputs[0]

    shifted = math("SUBTRACT", gain_socket, reference.HEAT_GATE_START, (-150, -400))
    gate = math("MULTIPLY", shifted, reference.HEAT_GATE_SCALE, (-10, -400), clamp=True,
                label="booster gate")
    below = math("SUBTRACT", gate, 1.0, (130, -400))
    scaled = math("MULTIPLY", influence, below, (270, -400))
    amount = math("ADD", scaled, 1.0, (410, -400), clamp=True, label="heat amount")

    # n1 * n2 - 0.5, then scaled by strength and amount.
    product = nodes.new("ShaderNodeVectorMath")
    product.operation = "MULTIPLY"
    product.location = (-150, 300)
    links.new(group_in.outputs["Noise 1"], product.inputs[0])
    links.new(group_in.outputs["Noise 2"], product.inputs[1])

    centred = nodes.new("ShaderNodeVectorMath")
    centred.operation = "SUBTRACT"
    centred.location = (10, 300)
    centred.label = "centre on 0.5"
    links.new(product.outputs[0], centred.inputs[0])
    centred.inputs[1].default_value = (reference.HEAT_NOISE_CENTRE,) * 3

    push = math("MULTIPLY", strength, amount, (410, 100), label="displacement")
    displaced = nodes.new("ShaderNodeVectorMath")
    displaced.operation = "SCALE"
    displaced.location = (560, 300)
    links.new(centred.outputs[0], displaced.inputs[0])
    links.new(push, displaced.inputs["Scale"])

    coordinate = nodes.new("ShaderNodeTexCoord")
    coordinate.location = (560, 500)
    total = nodes.new("ShaderNodeVectorMath")
    total.operation = "ADD"
    total.location = (720, 400)
    total.label = "uv + shimmer"
    links.new(coordinate.outputs["UV"], total.inputs[0])
    links.new(displaced.outputs[0], total.inputs[1])
    links.new(total.outputs[0], group_out.inputs["Glow UV"])
    return tree

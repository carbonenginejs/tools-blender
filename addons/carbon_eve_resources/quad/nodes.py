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

#: Inputs the shader reads per object rather than per material, as
#: (name, default, minimum, maximum, description).
#:
#: `AgeInWeeks` rather than the raw 0-1 dirt level, because weeks is what is
#: actually authored -- `EveShip2.weeksSinceCleaned` -- and the level is derived
#: from it by a curve. The group applies that curve so the number a user types
#: is the number the ship carries.
OBJECT_INPUTS = (
    ("Activation", 1.0, 0.0, 1.0,
     "Object activation; scales the glow (shipData.y)"),
    ("AgeInWeeks", 0.0, 0.0, 520.0,
     "Weeks since the hull was last cleaned. Zero is clean, and the resulting "
     "dirt level saturates toward 0.7 -- so past a few years more age changes "
     "little. Dirt also needs an authored MtlNDustDiffuseColor: it defaults to "
     "white, which looks clean however dirty the hull is"),
    ("EmissionStrength", 1.0, 0.0, 1000.0,
     "Carbon adds glow at full strength and the client blooms it; raise this "
     "to stand in for the bloom"),
)

#: The dust noise map's alpha is used separately from its colour, and Blender
#: exposes those as different sockets, so it needs an input of its own.
DUST_ALPHA = "DustNoiseAlpha"



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


def build_group(member: Optional[Member] = None) -> bpy.types.ShaderNodeTree:
    """Builds (or rebuilds) the node group for one family member."""

    member = member or load_family().member("quadv5.fx")
    tree = _new_group(f"{GROUP_PREFIX} {member.name}")
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

    # --- Object inputs ------------------------------------------------------
    object_panel = _panel(tree, "Object", panels)
    for name, default, minimum, maximum, description in OBJECT_INPUTS:
        _socket(tree, name, "NodeSocketFloat", description=description,
                default=default, panel=object_panel,
                min_value=minimum, max_value=maximum)

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
        else:
            _socket(tree, exposed, "NodeSocketFloat", description=note,
                    default=float(constant.default[0]), panel=panel)

    group_in = nodes.new("NodeGroupInput")
    group_in.location = (-1400, 0)
    group_out = nodes.new("NodeGroupOutput")
    group_out.location = (1200, 0)

    def has(name):
        return name in group_in.outputs

    def value(name):
        return group_in.outputs[name] if has(name) else None

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
    weights = []
    for layer, centre in enumerate(reference.MATERIAL_TENT_CENTRES):
        y = 600 - layer * 160
        shifted = math("SUBTRACT", material_x, centre, location=(-1000, y))
        scaled = math("MULTIPLY", shifted, reference.MATERIAL_TENT_SLOPE, location=(-840, y))
        absolute = math("ABSOLUTE", scaled, location=(-680, y))
        weights.append(math("SUBTRACT", reference.MATERIAL_TENT_OFFSET, absolute,
                            location=(-520, y), clamp=True,
                            label=f"Mtl{layer + 1} weight"))

    def blend4(prefix, suffix, location_y, *, scalar=False):
        """Weighted sum of the four per-layer constants."""

        total = None
        for layer, weight in enumerate(weights):
            name = f"{prefix}{layer + 1}{suffix}"
            if not has(name):
                return None
            x = -340 + layer * 40
            if scalar:
                term = math("MULTIPLY", value(name), weight,
                            location=(x, location_y - layer * 30))
                total = term if total is None else math("ADD", total, term,
                                                        location=(x + 20, location_y - layer * 30))
            else:
                term = vector("SCALE", value(name), None, location=(x, location_y - layer * 30))
                links.new(weight, term.node.inputs["Scale"])
                total = term if total is None else vector("ADD", total, term,
                                                          location=(x + 20, location_y - layer * 30))
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
    emission = None
    glow_color = "GeneralGlowColor" if has("GeneralGlowColor") else "GeneralHeatGlowColor"
    if has("GlowMap") and has(glow_color):
        glow_x = separate(value("GlowMap"), (-1200, -900))[0]
        powered = math("POWER", glow_x, reference.GLOW_INNER_EXPONENT * reference.GLOW_OUTER_EXPONENT,
                       location=(-1000, -900), label="glow ^2.4")
        scaled = math("MULTIPLY", powered, value("Activation"), location=(-840, -900))
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
        # Carbon carries an explicit F0 colour alongside the diffuse albedo.
        # Principled expresses a dielectric's F0 as
        # `0.08 * Specular IOR Level * Specular Tint`, so driving the tint with
        # the fresnel colour at full level gives F0 = 0.08 * colour.
        #
        # That 0.08 looks like a hard ceiling and mostly is not one, because
        # **EVE material colours are authored HDR** -- values above 1 are normal
        # and 3x is common. A fresnel authored at 3 lands at F0 = 0.24, and
        # 12.5 would reach a full mirror. So the mapping is not a faithful
        # equality; it is a rescale that happens to put HDR-authored values in a
        # sensible range. Whether it matches EVE's look cannot be judged until
        # the environment probe exists, since F0 shows almost entirely through
        # reflections.
        #
        # Nothing here clamps: Blender's sockets hold values above 1 and
        # preserve them through a .blend round-trip (verified). The one place
        # they are silently destroyed is the colour PICKER widget, which clamps
        # what a user drags even though typed values persist.
        principled.inputs["Specular IOR Level"].default_value = 1.0
        links.new(fresnel, principled.inputs["Specular Tint"])
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

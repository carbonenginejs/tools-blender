"""A reference implementation of the quad family's material composition.

This is the *oracle*, not the renderer. Blender builds the same arithmetic out
of shader nodes; this module computes it in plain Python so the two can be
compared numerically. Without that comparison "faithful" is an impression, and
this is a domain where a wrong answer renders perfectly plausibly.

Every literal here was read out of the emitted GLSL of the production body --
``quadv5.sm_depth``, ``Main.pass0`` pixel stage, with
``SPACE_OBJECT_PPT_ENABLED=SOPPT_ENABLED``. Nothing is rounded, and nothing is
tidied: the tent constants are not 3 and 1, and rounding them changes every
blended edge. Where a value looks like it wants to be a rational number the
measured float is kept, with the tidy form only in a comment.

Scope: the material composition, which is deterministic and has exactly one
right answer. NOT the lighting. The sun/GGX/environment-probe/fog/gamma tail
assumes a rasteriser with a screen-space shadow buffer and an SSAO texture,
none of which exist in Cycles, and reproducing it would mean ignoring the
scene's own lights. Blender lights the surface; this decides what the surface
is.

This module has no ``bpy`` dependency so it can be tested with the standard
library alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


Vec3 = tuple[float, float, float]
Vec4 = tuple[float, float, float, float]


# --- Measured constants -----------------------------------------------------
#
# Each is a literal in the shader, not a uniform, so none of them can be driven
# per object.

#: Tent-filter slope and offset for the four material weights.
#:
#: These are not 3.0 and 1.0, but the reason is not that the weights fail to
#: sum to one -- they sum to 1.0 within 1.4e-07 across the whole range. The
#: pair encodes a PLATEAU: each layer holds full weight within
#: `(offset - 1) / slope` = exactly 0.01 of its centre, and crossfades linearly
#: between plateaus. The two identities that pin it, both measured:
#:
#:     offset == 1.0 + slope * 0.01     (plateau half-width 0.01)
#:     offset == 0.5 + slope / 6.0      (midpoint splits exactly 0.5/0.5)
#:
#: Rounding them to 3 and 1 removes the plateau. *Inference, not measurement:*
#: the plateau is about the width of 8-bit quantisation error around 1/3 and
#: 2/3, so it likely exists to keep an authored region pure through texture
#: compression.
MATERIAL_TENT_SLOPE = 3.1914894580841064
MATERIAL_TENT_OFFSET = 1.0319149494171143

#: Half-width of the full-weight plateau around each centre.
MATERIAL_TENT_PLATEAU = (MATERIAL_TENT_OFFSET - 1.0) / MATERIAL_TENT_SLOPE

#: Centres of the four tents, at 0, 1/3, 2/3 and 1.
MATERIAL_TENT_CENTRES: Vec4 = (0.0, 0.3333333432674408, 0.6666666865348816, 1.0)

#: The fresnel colour the paint mask drives toward -- a dielectric F0 near 4%.
PAINT_FRESNEL_COLOR: Vec3 = (0.038384001702070236, 0.03935199975967407, 0.03916500136256218)

#: The gloss the paint mask drives toward. Unlike the material branch this is
#: flat: it is NOT multiplied by the roughness map.
PAINT_GLOSS = 0.4000000059604645

#: Added to the normal map's two channels before the unpack.
NORMAL_BIAS = 0.0020000000949949026

#: The dust noise map is tiled this many times over UV0, and its sample is
#: biased by +0.5 on all four channels including alpha.
DUST_TILING = 20.0
DUST_BIAS = 0.5

#: The glow map is squared and then raised to 1.2, so the exponent is 2.4.
GLOW_INNER_EXPONENT = 2.0
GLOW_OUTER_EXPONENT = 1.2000000476837158

#: Carbon's `EVE_SPACEOBJECT_DIRT_LEVEL_DEFAULT`.
DIRT_LEVEL_DEFAULT = 0.0

#: The dirt-level-from-age curve. Named so the node graph and this reference
#: cannot drift apart:
#:
#:     level = max(CEILING - 1 / (max(weeks, 0) ** EXPONENT + BIAS), 0)
DIRT_AGE_CEILING = 0.7
DIRT_AGE_EXPONENT = 0.65
DIRT_AGE_BIAS = 1.0 / 2.7

#: The dusty material's F0, baked into the shader like the paint one. Much
#: darker than the paint dielectric, which is what makes dirt read as dull.
DIRT_FRESNEL_COLOR: Vec3 = (0.01899999938905239, 0.017000000923871994, 0.014000000432133675)

#: The dusty material's gloss. Same value as the paint gloss, different role.
DIRT_GLOSS = 0.4000000059604645


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return low if value < low else high if value > high else value


def dirt_level_from_weeks(weeks: float, disabled: bool = False) -> float:
    """Carbon's dirt level for a ship that has not been cleaned in `weeks`.

    This is a CPU quantity, not a shader one: it lands in ``shipData.z``, which
    the pixel stage reads as ``cb4[12].z``. So "age in weeks" is an input to the
    object, and the shader only ever sees the resulting level.

    The curve saturates toward 0.7 and is negative below about one week, where
    the clamp holds it at zero -- so a freshly built hull is clean for a while
    rather than immediately dusty.

    Source: ccpwgl ``EveShip2.getDirtLevelFromWeeks``. Carbon's own C++ is the
    authority if the two ever disagree.
    """

    if disabled:
        return 0.0
    try:
        if weeks != weeks:  # NaN
            return 0.0
    except TypeError:
        return 0.0
    aged = pow(max(float(weeks), 0.0), DIRT_AGE_EXPONENT) + DIRT_AGE_BIAS
    return max(DIRT_AGE_CEILING - 1.0 / aged, 0.0)


def material_weights(material_map: float) -> Vec4:
    """The four material-layer weights for one `MaterialMap` sample.

    Four tent functions centred at 0, 1/3, 2/3 and 1::

        w = clamp(OFFSET - abs((m - centre) * SLOPE), 0, 1)

    A hull's visible regions are carved by this, not by the paint mask.
    """

    return tuple(  # type: ignore[return-value]
        clamp(MATERIAL_TENT_OFFSET - abs((material_map - centre) * MATERIAL_TENT_SLOPE))
        for centre in MATERIAL_TENT_CENTRES
    )


def paint_strength(paint_mask: float, general_data_x: float) -> float:
    """`PaintMaskMap.x * GeneralData.x`.

    ``GeneralData.x`` is the paint-mask influence and defaults to 1. This is
    untestable on a hull whose mask is empty: rendering with the influence at 1
    and at 0 then produces identical output.
    """

    return paint_mask * general_data_x


def blend_layers(weights: Sequence[float], values: Sequence[Sequence[float]]) -> tuple[float, ...]:
    """Weighted sum of the four per-layer constants."""

    if len(weights) != 4 or len(values) != 4:
        raise ValueError("the quad material model has exactly four layers")
    width = min(len(value) for value in values)
    return tuple(
        sum(weights[layer] * values[layer][component] for layer in range(4))
        for component in range(width)
    )


def lerp(a: Sequence[float], b: Sequence[float], factor: float) -> tuple[float, ...]:
    return tuple(x + (y - x) * factor for x, y in zip(a, b))


def material_color(
    weights: Sequence[float],
    diffuse_colors: Sequence[Sequence[float]],
    paint: float,
) -> Vec3:
    """The blended layer colour after the paint mask drives it toward white.

    Where the mask is set the material colour goes to white, so the albedo map
    passes through untinted rather than being coloured by the four layers.
    """

    blended = blend_layers(weights, diffuse_colors)[:3]
    return lerp(blended, (1.0, 1.0, 1.0), paint)  # type: ignore[return-value]


def fresnel_color(
    weights: Sequence[float],
    fresnel_colors: Sequence[Sequence[float]],
    paint: float,
) -> Vec3:
    """The blended layer F0 after the paint mask drives it toward the paint F0."""

    blended = blend_layers(weights, fresnel_colors)[:3]
    return lerp(blended, PAINT_FRESNEL_COLOR, paint)  # type: ignore[return-value]


def albedo(material: Sequence[float], albedo_map: Sequence[float]) -> Vec3:
    """`materialColor * AlbedoMap.rgb`."""

    return tuple(m * a for m, a in zip(material[:3], albedo_map[:3]))  # type: ignore[return-value]


def roughness(
    weights: Sequence[float],
    gloss_values: Sequence[Sequence[float]],
    roughness_map: float,
    paint: float,
) -> float:
    """Blended gloss, paint override, then `(1 - gloss)` squared.

    Only ``.x`` of each ``MtlNGloss`` is read; the rest is padding.
    """

    gloss = sum(weights[layer] * gloss_values[layer][0] for layer in range(4))
    combined = gloss * roughness_map
    combined = combined + (PAINT_GLOSS - combined) * paint
    linear = clamp(1.0 - combined)
    return linear * linear


def unpack_normal(normal_map: Sequence[float]) -> tuple[float, float]:
    """The two tangent-space components, biased then unpacked.

    Only two channels are stored. The third basis vector is the vertex normal
    added at an implicit weight of exactly 1.0 -- there is no
    ``sqrt(1 - x*x - y*y)`` anywhere in the shader, so a stock tangent-space
    normal-map node does not reproduce this. The caller finishes with::

        N = normalize(x * tangent + y * bitangent + vertexNormal)
    """

    return tuple(  # type: ignore[return-value]
        (channel + NORMAL_BIAS) * 2.0 - 1.0 for channel in normal_map[:2]
    )


def glow(glow_map: float) -> float:
    """`pow(GlowMap.x, 2.4)`, written in the shader as a square then `pow(_, 1.2)`."""

    return pow(glow_map * glow_map, GLOW_OUTER_EXPONENT)


def emissive(glow_map: float, glow_color: Sequence[float], activation: float) -> Vec3:
    """Glow scaled by the object's activation, which is `shipData.y`.

    Activation is per object, not a material constant, so a static export has to
    decide it rather than read it from the material.
    """

    strength = glow(glow_map) * activation
    return tuple(channel * strength for channel in glow_color[:3])  # type: ignore[return-value]


def dust_noise_uv(uv: Sequence[float]) -> tuple[float, float]:
    """`uv * 20.0` -- the dust noise map has its own tiling."""

    return (uv[0] * DUST_TILING, uv[1] * DUST_TILING)


def dust_noise(sample: Sequence[float]) -> Vec4:
    """The dust noise sample, biased by +0.5 on all four channels.

    The bias applies to alpha too, which is easy to miss because the alpha
    channel is used separately from the other three.
    """

    return tuple(channel + DUST_BIAS for channel in sample[:4])  # type: ignore[return-value]


def dust_diffuse_color(
    weights: Sequence[float],
    dust_colors: Sequence[Sequence[float]],
) -> Vec3:
    """The dusty material's diffuse colour.

    The *same* four tent weights are applied to `Mtl1-4DustDiffuseColor` as to
    the clean `Mtl1-4DiffuseColor`, so dust is a second material layer set
    rather than a tint on top of the first.
    """

    return blend_layers(weights, dust_colors)[:3]  # type: ignore[return-value]


def dusty_albedo(
    albedo_map: Sequence[float],
    dust_color: Sequence[float],
    dust_noise_x: float,
) -> Vec3:
    """The dusty material's albedo: `AlbedoMap * dustColour * noise.x`.

    The noise channel is what stops dirt being a flat tint. Note that
    `MtlNDustDiffuseColor` defaults to **white**, so on Carbon's bare defaults a
    fully dirty surface shows the untinted albedo and reads as *clean*. The
    authored SOF value is what makes dirt look like dirt.
    """

    return tuple(  # type: ignore[return-value]
        a * d * dust_noise_x for a, d in zip(albedo_map[:3], dust_color[:3])
    )


def dusty_fresnel(dust_noise_y: float) -> Vec3:
    """The dusty material's F0: `noise.y * DIRT_FRESNEL_COLOR`.

    Not derived from the material's own fresnel colour at all -- dirt has its
    own baked, much darker F0, which is most of why a dirty surface stops
    looking reflective.
    """

    return tuple(dust_noise_y * channel for channel in DIRT_FRESNEL_COLOR)  # type: ignore[return-value]


def dusty_roughness(roughness_map: float, dust_noise_z: float) -> float:
    """The dusty material's roughness.

    Uses the baked `DIRT_GLOSS` rather than the blended material gloss, and the
    paint mask does not enter: dirt sits on top of paint::

        clamp(1 - RoughnessMap * noise.z * DIRT_GLOSS) ** 2
    """

    linear = clamp(1.0 - roughness_map * dust_noise_z * DIRT_GLOSS)
    return linear * linear


def dirt_mask(dirt_map: float, dust_noise_w: float, dirt_level: float) -> float:
    """How much of this texel is dirty.

    The dirt texture is modulated by the dust noise map's ALPHA channel -- the
    one that also carries the `+0.5` bias -- and then divided by
    `1 - dirtLevel`, so the object's dirt level widens the mask rather than
    scaling it. `dirtLevel` reaches the shader as ``shipData.z`` and comes from
    `dirt_level_from_weeks`.

    At `dirtLevel` 0 the mask is just the texture; as the level rises toward 1
    the divisor shrinks and the mask saturates over more of the surface.
    """

    divisor = 1.0 - dirt_level
    if divisor <= 0.0:
        return 1.0
    return clamp((dirt_map * dust_noise_w) / divisor)


def dirt_weights(mask: float) -> tuple[float, float]:
    """The authored clean and dusty weights: `((1 - mask) ** 3, mask)`.

    They deliberately do not sum to one.
    """

    inverse = 1.0 - mask
    return (inverse * inverse * inverse, mask)


def dirt_blend_factor(mask: float) -> float:
    """How dusty a surface-parameter blend should be, for a given mask.

    A consumer that lights once cannot blend two lit results, but it can carry
    the authored *balance* across. Production weights the clean side by
    `(1 - mask) ** 3` and the dusty side by `mask`, so the dusty share of the
    total is::

        mask / ((1 - mask) ** 3 + mask)

    That is much dirtier than the mask alone: at a mask of 0.5 the surface is
    80% dusty, not 50%. Using the raw mask as a mix factor -- the obvious thing
    -- makes dirt far too weak, which is exactly how it first looked.
    """

    clean, dusty = dirt_weights(mask)
    total = clean + dusty
    return dusty / total if total > 0.0 else 0.0


def dirt_energy(mask: float) -> float:
    """The overall dimming the authored weights apply: `(1 - mask) ** 3 + mask`.

    Below one across the mid-range -- 0.625 at a mask of 0.5 -- so a half-dirty
    texel is genuinely darker than either side. Splitting the authored weights
    into a blend factor and this scale reproduces the production result exactly
    wherever the lighting is linear in the quantity, which the diffuse term is.
    """

    clean, dusty = dirt_weights(mask)
    return clean + dusty


def combine_dirt(clean: Sequence[float], dusty: Sequence[float], mask: float) -> tuple[float, ...]:
    """Blend a clean and a dusty result by the dirt mask.

    **This is where a Blender port necessarily diverges, and the divergence is
    deliberate.** The shader evaluates the ENTIRE lighting twice -- once for the
    clean material and once for the dusty one -- and blends the two lit results::

        colour = pow(1 - mask, 3) * cleanLit + mask * dustyLit

    Note the weights do not sum to one: at `mask` 0.5 they total 0.625, so a
    half-dirty texel is darker than either side. That is an authored curve, not
    an error.

    A consumer that outputs surface parameters for Blender to light has no two
    lit results to blend, so it blends the parameters instead and lights once.
    That is a different operation and will not match a client screenshot in the
    mid-range. It is the same trade already made by not reproducing the sun,
    the environment probe or the screen-space buffers.

    This function implements the authored curve, for the reference path and for
    anyone reproducing the full chain.
    """

    inverse = 1.0 - mask
    clean_weight = inverse * inverse * inverse
    return tuple(c * clean_weight + d * mask for c, d in zip(clean, dusty))


#: Sampler address modes, as Carbon stores them after
#: `EveSOFDataPatternLayer.ToAddressMode`. A pattern projection sets U and V
#: independently, so all nine combinations are reachable.
WRAP_REPEAT = 1
WRAP_EDGE = 3
WRAP_BORDER = 4

#: The projection types authored on the pattern layer, and what they convert to.
PROJECTION_TO_WRAP = {0: WRAP_REPEAT, 1: WRAP_EDGE, 2: WRAP_BORDER}


def wrap_coordinate(value: float, mode: int) -> float:
    """One projected coordinate after its wrap mode.

    `REPEAT` tiles, and both clamping modes pin the lookup to the edge -- they
    differ in what happens *outside*, which is `pattern_coverage`'s job rather
    than this one's.
    """

    if mode == WRAP_REPEAT:
        return value - int(value // 1) * 1.0 if value >= 0 else value - (value // 1)
    return clamp(value)


def pattern_coverage(u: float, v: float, mode_u: int, mode_v: int) -> float:
    """Whether a projected texel is covered at all, given the wrap modes.

    Only `CLAMP_TO_BORDER` can produce "nothing here": outside `[0, 1]` the
    lookup returns the border, which for pattern projections is black, and the
    pattern therefore covers nothing. `REPEAT` and `CLAMP_TO_EDGE` always cover.

    Returned as a factor to multiply the sampled mask by, so a consumer whose
    renderer lacks a border mode gets the same answer without emulating one.
    """

    for value, mode in ((u, mode_u), (v, mode_v)):
        if mode == WRAP_BORDER and not (0.0 <= value <= 1.0):
            return 0.0
    return 1.0


def sails_selector(
    uv: Sequence[float],
    material_map: float,
    sails_sample_at,
    sails_detail_data: Sequence[float],
) -> float:
    """`quadsailsv5`: the sail pattern re-selects which material layer is used.

    The detail texture is tiled by ``SailsDetailData.x`` and rotated by
    ``SailsDetailData.y`` radians, then blended over the `MaterialMap` selector
    by the tent weight of **layer 1** -- so the sail pattern only takes effect
    where the first material is selected, which is what makes that region "the
    sail area"::

        uv'      = rotate(uv * data.x, data.y)
        selector = mix(MaterialMap.x, SailsDetailMap(uv').x, weight1)

    The four material weights are then computed from `selector` rather than
    from `MaterialMap.x` directly. It does not add a material; it changes which
    one is chosen.

    `sails_sample_at` is called with the rotated UV and returns the texture's
    red channel, so the caller owns sampling.
    """

    import math

    tiling, angle = sails_detail_data[0], sails_detail_data[1]
    u, v = uv[0] * tiling, uv[1] * tiling
    sin_a, cos_a = math.sin(angle), math.cos(angle)
    rotated = (u * cos_a - v * sin_a, u * sin_a + v * cos_a)

    sails = sails_sample_at(rotated)
    weight1 = clamp(MATERIAL_TENT_OFFSET - abs(material_map * MATERIAL_TENT_SLOPE))
    return material_map + weight1 * (sails - material_map)


@dataclass(frozen=True, slots=True)
class Surface:
    """What the material composition produces, for Blender to light."""

    albedo: Vec3
    roughness: float
    fresnel_color: Vec3
    emissive: Vec3
    normal_xy: tuple[float, float]
    material_weights: Vec4
    paint_strength: float


def compose(
    *,
    material_map: float,
    paint_mask: float,
    albedo_map: Sequence[float],
    roughness_map: float,
    normal_map: Sequence[float],
    glow_map: float,
    diffuse_colors: Sequence[Sequence[float]],
    fresnel_colors: Sequence[Sequence[float]],
    gloss_values: Sequence[Sequence[float]],
    general_data: Sequence[float] = (1.0, 0.0, 0.0, 0.0),
    glow_color: Sequence[float] = (1.0, 1.0, 1.0, 1.0),
    activation: float = 1.0,
) -> Surface:
    """The base quad material composition for one texel.

    Covers the arithmetic shared by every family member. Dirt, dust, patterns,
    heat, detail, sails, oil, wreck and glass each add to this and are not
    implemented here yet; each needs its own read of its own emitted GLSL.
    """

    weights = material_weights(material_map)
    paint = paint_strength(paint_mask, general_data[0])
    material = material_color(weights, diffuse_colors, paint)

    return Surface(
        albedo=albedo(material, albedo_map),
        roughness=roughness(weights, gloss_values, roughness_map, paint),
        fresnel_color=fresnel_color(weights, fresnel_colors, paint),
        emissive=emissive(glow_map, glow_color, activation),
        normal_xy=unpack_normal(normal_map),
        material_weights=weights,
        paint_strength=paint,
    )

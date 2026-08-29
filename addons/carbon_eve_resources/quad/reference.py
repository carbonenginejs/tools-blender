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
import math
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

#: How far past its projection a CLAMPED pattern mask fades out, in UV.
#:
#: A clamp repeats one row of texels for the rest of the hull. The client does
#: not show that row; we did, as a straight line across the plate, because a
#: hard clamp holds the edge texel at full strength however far outside the
#: projection the surface runs.
#:
#: This is a SOFTENING, not a mode: the authored wrap mode is untouched, which
#: it has to be -- the modes are authored per mask and are not ours to choose.
#: Zero restores the hard edge exactly.
PATTERN_EDGE_BLEND = 0.05

#: How much the authored glow colours are boosted before they are emitted.
#:
#: The authored values are the colour a light IS, not how brightly it burns,
#: and the engine multiplies them on the way in -- ten for a general glow, a
#: hundred for a heat glow, under a comment that says "Boost lights".
#:
#: Without it every light on a hull is an order of magnitude too dim, which
#: reads as the lights being broken rather than as a missing constant.
GENERAL_GLOW_MULTIPLIER = 10.0
GENERAL_HEAT_GLOW_MULTIPLIER = 100.0

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


# --- Heat glow -------------------------------------------------------------
#
# `quadheatv5` and `quadheatdetailv5` add a shimmer over the glow map, gated by
# the object's booster gain. `MtlNHeatGlowData` is four separate quantities
# rather than a colour, blended by the same four tent weights as everything
# else: (gate influence, scroll speed, noise tiling, distortion strength).

#: The booster gain window the heat gate opens across. Measured as a subtract
#: of 0.005 followed by a multiply of 66.667, so heat is fully on by a gain of
#: 0.02 -- a very narrow window just above zero, which is why heat reads as a
#: switch rather than a fade.
HEAT_GATE_START = 0.004999999888241291
HEAT_GATE_SCALE = 66.66667175292969

#: The noise product is centred here before it displaces the lookup, so an
#: average noise leaves the glow where it is.
HEAT_NOISE_CENTRE = 0.5


def heat_gate(booster_gain: float, influence: float) -> float:
    """How much heat shows, from the object's booster gain.

    `influence` is `MtlNHeatGlowData.x` blended across the layers: at zero the
    material ignores the gate and always glows, at one it follows the boosters
    completely.
    """

    gate = clamp((booster_gain - HEAT_GATE_START) * HEAT_GATE_SCALE)
    return clamp(influence * (gate - 1.0) + 1.0)


def heat_offset(
    uv: Sequence[float],
    time: float,
    data: Sequence[float],
    amount: float,
    sample_noise,
) -> tuple[float, float]:
    """How far the glow lookup is displaced by the shimmer.

    Two taps of the noise map scroll in OPPOSITE directions and multiply, which
    is what stops the shimmer looking like a texture sliding past. `data.y` is
    the speed, `data.z` the tiling, `data.w` the displacement strength.
    """

    speed, tiling, strength = data[1], data[2], data[3]
    forward = sample_noise(((uv[0] + speed * time) * tiling, (uv[1] + speed * time) * tiling))
    backward = sample_noise(((uv[0] - speed * time) * tiling, (uv[1] - speed * time) * tiling))
    scale = strength * amount
    return (
        scale * (forward[0] * backward[0] - HEAT_NOISE_CENTRE),
        scale * (forward[1] * backward[1] - HEAT_NOISE_CENTRE),
    )


def heat_emissive(
    glow_at,
    uv: Sequence[float],
    offset: Sequence[float],
    colour: Sequence[float],
    amount: float,
    activation: float,
) -> Vec3:
    """The heat term, which distorts the GLOW map rather than adding a texture.

    The same `pow(glow, 2.4)` and activation scaling as the base glow -- heat
    reuses the glow map, sampled at a displaced coordinate, which is why a hull
    with no glow detail shows no heat however hot it is.
    """

    glow = glow_at((uv[0] + offset[0], uv[1] + offset[1]))
    strength = amount * pow(glow * glow, GLOW_OUTER_EXPONENT) * activation
    return tuple(channel * strength for channel in colour[:3])  # type: ignore[return-value]


#: The kill counter's grid, measured from `decalcounterv5`'s pixel stage.
#:
#: The shader works in a coordinate that is the decal's projected UV shifted
#: into ``[0, 2]``, and multiplies it by 4.5 and 1.5 before truncating. Over the
#: decal's own ``[0, 1]`` that is NINE columns and THREE rows.
KILL_COUNTER_COLUMNS = 9.0
KILL_COUNTER_ROWS = 3.0

#: ``log2(10)``, as the shader spells it: it raises ten to a power by
#: ``exp2(x * 3.32192802)`` rather than calling a decimal power.
KILL_COUNTER_LOG2_TEN = 3.32192802


def kill_counter_digit(count: float, row: int) -> float:
    """The decimal digit a row of the kill counter shows.

    Row 0 is units, row 1 tens, row 2 hundreds, which is why the shader raises
    ten to the row. The half added before truncating is the shader's own, and it
    is what keeps a count that arrives as 6.999998 from reading as five.
    """

    place = pow(2.0, KILL_COUNTER_LOG2_TEN * row)
    following = pow(2.0, KILL_COUNTER_LOG2_TEN * (row + 1.0))
    share = count / following
    # The shader takes fract() of the absolute value and restores the sign, so a
    # negative count folds the same way rather than clamping.
    folded = math.fmod(abs(share), 1.0)
    if share < 0.0:
        folded = -folded
    return float(int((folded * following + 0.5) / place))


def kill_counter_coverage(uv: Sequence[float], count: float) -> float:
    """Whether one point of the counter decal draws a mark.

    The counter is not a row of glyphs: each of the three rows draws as many
    TALLY MARKS as its decimal digit, up to nine, and the shader DISCARDS the
    rest. So 27 kills is seven marks on the units row and two on the tens row.

    Zero outside the decal's own box, because the shader also discards there.
    """

    point = (uv[0] * 2.0, uv[1] * 2.0)
    if not (0.0 <= point[0] <= 2.0 and 0.0 <= point[1] <= 2.0):
        return 0.0
    column = float(int(point[0] * 4.5))
    row = float(int(point[1] * 1.5))
    digit = kill_counter_digit(count, row)
    # The shader discards where the digit is BELOW the column's centre, so a
    # digit of three lights columns 0, 1 and 2.
    return 0.0 if digit < column + 0.5 else 1.0


def kill_counter_mark_uv(uv: Sequence[float]) -> tuple:
    """Where one mark of the counter samples its texture.

    Nine repeats across, one down: the horizontal scale matches the column
    count, so each column samples the whole mark.
    """

    return (uv[0] * 2.0 * 4.5, uv[1] * 2.0 * 0.5)


def kill_counter_alpha(mark: float, intensity: float, coverage: float) -> float:
    """The counter's opacity: the mark texture, scaled, then SQUARED.

    The square is the shader's, applied after both scalings, and it is what
    makes the marks read as hard-edged rather than as a soft smear.
    """

    scaled = mark * intensity
    return coverage * scaled * scaled


def hole_interior_direction(point: Sequence[float], view: Sequence[float]) -> tuple:
    """Where a hull breach's interior is sampled, or None if the ray misses.

    `decalholev5` fakes depth with a UNIT SPHERE in decal space. It walks the
    view ray to where it leaves that sphere and samples the interior cube along
    the direction of the exit point -- so the interior parallaxes as the camera
    moves and the breach reads as a hole rather than as a sticker.

    The shader starts the ray at the eye; starting it at the surface point gives
    the same exit, because both lie on the same line, so this takes the point as
    the origin and the view direction as the ray.

        t   = -dot(P, d) + sqrt(dot(P, d)^2 - |P|^2 + 1)
        hit = normalize(P + t * d)

    Returns None where the discriminant is negative, which is the shader's own
    `discard`: the ray misses the sphere entirely.
    """

    px, py, pz = point[0], point[1], point[2]
    length = math.sqrt(view[0] * view[0] + view[1] * view[1] + view[2] * view[2])
    if length == 0.0:
        return None
    dx, dy, dz = view[0] / length, view[1] / length, view[2] / length
    along = px * dx + py * dy + pz * dz
    discriminant = along * along - (px * px + py * py + pz * pz) + 1.0
    if discriminant < 0.0:
        return None
    distance = -along + math.sqrt(discriminant)
    hit = (px + distance * dx, py + distance * dy, pz + distance * dz)
    size = math.sqrt(hit[0] * hit[0] + hit[1] * hit[1] + hit[2] * hit[2])
    if size == 0.0:
        return None
    return (hit[0] / size, hit[1] / size, hit[2] / size)


def hole_colour(hole_rim: float, hole_blend: float, interior: float,
                glow: Sequence[float]) -> tuple:
    """The breach's colour: the rim, the interior, and the blend between them.

        colour = DecalGlowColor * mix(holeMap.x, interiorCube.a, holeMap.w)

    The interior lives in the cube's ALPHA channel, not its colour, which is
    why a converter that keeps only RGB throws the interior away.
    """

    mixed = hole_rim + (interior - hole_rim) * hole_blend
    return tuple(channel * mixed for channel in glow[:3])

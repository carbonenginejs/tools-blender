from pathlib import Path
import sys
import unittest


ADDONS = Path(__file__).resolve().parents[1] / "addons"
if str(ADDONS) not in sys.path:
    sys.path.insert(0, str(ADDONS))

from carbon_eve_resources.quad import (  # noqa: E402
    QuadInterfaceError,
    decals,
    load_family,
    normalize_shader_name,
    reference,
)


WHITE = (1.0, 1.0, 1.0, 1.0)
GLOSS_DEFAULT = 0.4


class NormalizeShaderName(unittest.TestCase):

    def test_strips_suffix_prefix_and_path(self):
        for spelling in (
            "quadv5.fx",
            "QuadV5.fx",
            "skinned_quadv5.fx",
            "unpackedskinned_quadv5.fx",
            "res:/graphics/effect/managed/space/spaceobject/v5/quad/quadv5.fx",
        ):
            self.assertEqual(normalize_shader_name(spelling), "quadv5", spelling)

    def test_prefixes_do_not_change_the_member(self):
        # The prefixes change tangent packing and skinning, not the pixel
        # stage's resource set, so they must resolve to the same member.
        family = load_family()
        self.assertIs(family.member("skinned_quadheatv5.fx"), family.member("quadheatv5.fx"))


class FamilyData(unittest.TestCase):

    def setUp(self):
        self.family = load_family()

    def test_measured_at_the_production_permutation(self):
        # .sm_hi omits dirt, dust, patterns, local lights and the SH term while
        # still being a valid shader, so the tier is part of the claim.
        self.assertEqual(self.family.tier, "sm_depth")
        self.assertEqual(self.family.permutation.get("SPACE_OBJECT_PPT_ENABLED"), "SOPPT_ENABLED")

    def test_every_member_is_present(self):
        self.assertEqual(len(self.family.members), 10)
        self.assertIn("quadv5", self.family.members)

    def test_quadv5_adds_nothing_of_its_own(self):
        # quadv5 IS the base: every texture it binds is bound by at least one
        # other member too, so it contributes no feature of its own. That is
        # why one base group plus per-member features is the shape the
        # containers have, rather than a convenience.
        base = self.family.member("quadv5.fx")
        others = self.family.members.items()
        for texture in base.textures:
            shared = any(
                texture in member.textures
                for name, member in others
                if name != "quadv5"
            )
            self.assertTrue(shared, f"{texture} is unique to quadv5")

    def test_the_common_textures_are_the_measured_eleven(self):
        common = set(self.family.common_textures())
        for expected in ("AlbedoMap", "RoughnessMap", "NormalMap", "MaterialMap",
                         "GlowMap", "DirtMap", "DustNoiseMap"):
            self.assertIn(expected, common)
        # PaintMaskMap is only 8/10 -- absent from environment and instanced.
        self.assertNotIn("PaintMaskMap", common)

    def test_dirt_and_dust_are_baseline_not_debug(self):
        # Measured on .sm_hi these look absent from the whole family.
        for name in ("quadv5", "quadglassv5", "quadwreckv5"):
            member = self.family.members[name]
            self.assertIn("DirtMap", member.textures, name)
            self.assertIn("Mtl1DustDiffuseColor", member.constants, name)

    def test_carbon_defaults_are_white_with_gloss_point_four(self):
        base = self.family.member("quadv5.fx")
        self.assertEqual(base.constant("Mtl1DiffuseColor").default, WHITE)
        self.assertEqual(base.constant("Mtl1FresnelColor").default, WHITE)
        # float32 0.4 is 0.4000000059604645; compare as a number, not a literal.
        self.assertAlmostEqual(base.constant("Mtl1Gloss").default[0], GLOSS_DEFAULT, places=6)
        self.assertEqual(base.constant("GeneralData").default, (1.0, 0.0, 0.0, 0.0))

    def test_shared_head_offsets_agree_across_members(self):
        # vec4[0..27] is canonical family-wide; a member lacking a feature
        # leaves a hole rather than repacking.
        for name in ("Mtl1DiffuseColor", "Mtl1Gloss", "Mtl1DustDiffuseColor"):
            seen = {
                member.constants[name].vec4
                for member in self.family.members.values()
                if name in member.constants
            }
            self.assertEqual(len(seen), 1, f"{name} moved between members: {seen}")

    def test_heat_members_swap_the_glow_colour(self):
        heat = self.family.members["quadheatv5"]
        self.assertNotIn("GeneralGlowColor", heat.constants)
        self.assertIn("GeneralHeatGlowColor", heat.constants)

    def test_unknown_member_is_none(self):
        self.assertIsNone(self.family.member("nosuchshaderv5.fx"))

    def test_refuses_a_newer_document(self, ):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "family.json"
            path.write_text(json.dumps({
                "schema": "carbon.quad-family-interface",
                "version": 99,
                "members": {},
            }), encoding="utf-8")
            with self.assertRaises(QuadInterfaceError):
                load_family(path)


class MaterialWeights(unittest.TestCase):

    def test_each_centre_peaks_at_its_own_layer(self):
        for layer, centre in enumerate(reference.MATERIAL_TENT_CENTRES):
            weights = reference.material_weights(centre)
            self.assertAlmostEqual(weights[layer], 1.0, places=6)

    def test_weights_sum_to_one_across_the_range(self):
        for step in range(0, 1001):
            total = sum(reference.material_weights(step / 1000.0))
            self.assertAlmostEqual(total, 1.0, places=6)

    def test_each_centre_has_a_plateau_of_full_weight(self):
        # The slope and offset are not 3 and 1 because they encode a plateau:
        # a layer holds full weight within 0.01 of its centre, which is about
        # the width of 8-bit quantisation error around 1/3 and 2/3.
        self.assertAlmostEqual(reference.MATERIAL_TENT_PLATEAU, 0.01, places=6)
        for layer, centre in enumerate(reference.MATERIAL_TENT_CENTRES):
            inside = centre + reference.MATERIAL_TENT_PLATEAU * 0.9
            self.assertAlmostEqual(reference.material_weights(inside)[layer], 1.0, places=6)

    def test_the_two_identities_that_pin_the_constants(self):
        slope = reference.MATERIAL_TENT_SLOPE
        offset = reference.MATERIAL_TENT_OFFSET
        self.assertAlmostEqual(offset, 1.0 + slope * 0.01, places=6)
        self.assertAlmostEqual(offset, 0.5 + slope / 6.0, places=6)

    def test_the_midpoint_splits_evenly(self):
        weights = reference.material_weights(1.0 / 6.0)
        self.assertAlmostEqual(weights[0], 0.5, places=6)
        self.assertAlmostEqual(weights[1], 0.5, places=6)

    def test_weights_stay_clamped(self):
        for sample in (-1.0, 0.0, 0.17, 0.5, 0.83, 1.0, 2.0):
            for weight in reference.material_weights(sample):
                self.assertGreaterEqual(weight, 0.0)
                self.assertLessEqual(weight, 1.0)


class PaintOverride(unittest.TestCase):

    def test_full_paint_drives_material_colour_to_white(self):
        weights = reference.material_weights(0.0)
        colors = [(0.1, 0.2, 0.3, 1.0)] * 4
        self.assertEqual(reference.material_color(weights, colors, 1.0), (1.0, 1.0, 1.0))

    def test_full_paint_drives_fresnel_to_the_baked_dielectric(self):
        weights = reference.material_weights(0.0)
        colors = [(0.9, 0.9, 0.9, 1.0)] * 4
        self.assertEqual(
            reference.fresnel_color(weights, colors, 1.0),
            reference.PAINT_FRESNEL_COLOR,
        )

    def test_an_empty_mask_changes_nothing(self):
        # A hull whose paint mask is zero renders identically at influence 1
        # and 0, so this path is untestable on such a hull in a render.
        weights = reference.material_weights(0.0)
        colors = [(0.25, 0.5, 0.75, 1.0)] * 4
        at_one = reference.material_color(weights, colors, reference.paint_strength(0.0, 1.0))
        at_zero = reference.material_color(weights, colors, reference.paint_strength(0.0, 0.0))
        self.assertEqual(at_one, at_zero)


class Roughness(unittest.TestCase):

    def test_paint_gloss_is_flat_and_not_multiplied_by_the_map(self):
        weights = reference.material_weights(0.0)
        gloss = [(1.0, 1.0, 1.0, 1.0)] * 4
        # Under full paint the roughness map must not matter.
        dark = reference.roughness(weights, gloss, 0.0, 1.0)
        bright = reference.roughness(weights, gloss, 1.0, 1.0)
        self.assertAlmostEqual(dark, bright, places=6)

    def test_material_gloss_is_multiplied_by_the_map(self):
        weights = reference.material_weights(0.0)
        gloss = [(1.0, 1.0, 1.0, 1.0)] * 4
        self.assertNotAlmostEqual(
            reference.roughness(weights, gloss, 0.0, 0.0),
            reference.roughness(weights, gloss, 1.0, 0.0),
            places=3,
        )

    def test_carbon_defaults_give_a_plausible_surface(self):
        family = load_family()
        base = family.member("quadv5.fx")
        gloss = [base.constant(f"Mtl{n}Gloss").default for n in (1, 2, 3, 4)]
        weights = reference.material_weights(0.0)
        value = reference.roughness(weights, gloss, 1.0, 0.0)
        self.assertGreater(value, 0.0)
        self.assertLess(value, 1.0)


class NormalUnpack(unittest.TestCase):

    def test_two_channels_only_and_biased_before_unpack(self):
        x, y = reference.unpack_normal((0.5, 0.5, 0.5))
        self.assertAlmostEqual(x, (0.5 + reference.NORMAL_BIAS) * 2.0 - 1.0, places=7)
        self.assertAlmostEqual(y, x, places=7)

    def test_there_is_no_reconstructed_z(self):
        # A stock tangent-space normal map node consumes three channels; this
        # shader reads two and adds the vertex normal at weight 1.0.
        self.assertEqual(len(reference.unpack_normal((0.1, 0.2, 0.9))), 2)


class Dust(unittest.TestCase):

    def test_noise_is_tiled_twenty_times(self):
        self.assertEqual(reference.dust_noise_uv((0.5, 0.25)), (10.0, 5.0))

    def test_bias_applies_to_alpha_too(self):
        biased = reference.dust_noise((0.0, 0.0, 0.0, 0.0))
        self.assertEqual(biased, (0.5, 0.5, 0.5, 0.5))


class Glow(unittest.TestCase):

    def test_exponent_is_two_point_four(self):
        self.assertAlmostEqual(reference.glow(0.5), pow(0.5, 2.4), places=6)

    def test_activation_scales_emission(self):
        lit = reference.emissive(1.0, (1.0, 0.5, 0.25), 1.0)
        off = reference.emissive(1.0, (1.0, 0.5, 0.25), 0.0)
        self.assertEqual(off, (0.0, 0.0, 0.0))
        self.assertAlmostEqual(lit[0], 1.0, places=6)


class DirtLevel(unittest.TestCase):

    def test_a_fresh_hull_is_clean(self):
        self.assertEqual(reference.dirt_level_from_weeks(0.0), 0.0)

    def test_it_saturates_toward_zero_point_seven(self):
        self.assertLess(reference.dirt_level_from_weeks(10_000.0), 0.7)
        self.assertGreater(reference.dirt_level_from_weeks(10_000.0), 0.69)

    def test_it_increases_with_age(self):
        samples = [reference.dirt_level_from_weeks(w) for w in (1, 4, 12, 52, 260)]
        self.assertEqual(samples, sorted(samples))

    def test_disabled_is_clean(self):
        self.assertEqual(reference.dirt_level_from_weeks(52.0, disabled=True), 0.0)

    def test_negative_and_nan_are_clean(self):
        self.assertEqual(reference.dirt_level_from_weeks(-5.0), 0.0)
        self.assertEqual(reference.dirt_level_from_weeks(float("nan")), 0.0)


class Compose(unittest.TestCase):

    def test_carbon_defaults_produce_the_albedo_map_untinted(self):
        # Drawn on Carbon's defaults an object shows its maps with no material
        # on top: correct, and not recognisable as itself.
        family = load_family()
        base = family.member("quadv5.fx")
        diffuse = [base.constant(f"Mtl{n}DiffuseColor").default for n in (1, 2, 3, 4)]
        fresnel = [base.constant(f"Mtl{n}FresnelColor").default for n in (1, 2, 3, 4)]
        gloss = [base.constant(f"Mtl{n}Gloss").default for n in (1, 2, 3, 4)]

        surface = reference.compose(
            material_map=0.0,
            paint_mask=0.0,
            albedo_map=(0.2, 0.4, 0.6),
            roughness_map=1.0,
            normal_map=(0.5, 0.5),
            glow_map=0.0,
            diffuse_colors=diffuse,
            fresnel_colors=fresnel,
            gloss_values=gloss,
            general_data=base.constant("GeneralData").default,
        )
        for produced, authored in zip(surface.albedo, (0.2, 0.4, 0.6)):
            self.assertAlmostEqual(produced, authored, places=6)

    def test_zero_is_not_a_neutral_material(self):
        # A zero-filled material is a black object with no gloss, which reads as
        # a broken shader rather than as a missing default.
        zero = [(0.0, 0.0, 0.0, 0.0)] * 4
        surface = reference.compose(
            material_map=0.0,
            paint_mask=0.0,
            albedo_map=(1.0, 1.0, 1.0),
            roughness_map=1.0,
            normal_map=(0.5, 0.5),
            glow_map=0.0,
            diffuse_colors=zero,
            fresnel_colors=zero,
            gloss_values=zero,
        )
        self.assertEqual(surface.albedo, (0.0, 0.0, 0.0))
        self.assertAlmostEqual(surface.roughness, 1.0, places=6)


if __name__ == "__main__":
    unittest.main()


class Dirt(unittest.TestCase):

    def test_dust_uses_the_same_weights_as_the_clean_layers(self):
        weights = reference.material_weights(0.5)
        colors = [(0.1, 0.1, 0.1, 1.0), (0.2, 0.2, 0.2, 1.0),
                  (0.3, 0.3, 0.3, 1.0), (0.4, 0.4, 0.4, 1.0)]
        self.assertEqual(
            reference.dust_diffuse_color(weights, colors),
            reference.blend_layers(weights, colors)[:3],
        )

    def test_mask_is_the_texture_when_the_object_is_clean(self):
        self.assertAlmostEqual(reference.dirt_mask(0.5, 1.0, 0.0), 0.5, places=6)

    def test_dirt_level_widens_the_mask(self):
        low = reference.dirt_mask(0.4, 1.0, 0.0)
        high = reference.dirt_mask(0.4, 1.0, 0.5)
        self.assertGreater(high, low)

    def test_the_noise_alpha_modulates_the_mask(self):
        self.assertAlmostEqual(reference.dirt_mask(1.0, 0.25, 0.0), 0.25, places=6)

    def test_mask_stays_clamped_at_extreme_dirt(self):
        self.assertEqual(reference.dirt_mask(1.0, 1.0, 1.0), 1.0)
        self.assertEqual(reference.dirt_mask(0.5, 1.0, 0.9), 1.0)

    def test_the_blend_weights_do_not_sum_to_one(self):
        # An authored curve, not an error: a half-dirty texel is darker than
        # either side. Asserted so it is not "fixed" into a plain mix.
        mid = reference.combine_dirt((1.0,), (1.0,), 0.5)
        self.assertAlmostEqual(mid[0], 0.625, places=6)

    def test_the_ends_are_exact(self):
        clean = reference.combine_dirt((0.8, 0.6, 0.4), (0.1, 0.1, 0.1), 0.0)
        dusty = reference.combine_dirt((0.8, 0.6, 0.4), (0.1, 0.2, 0.3), 1.0)
        self.assertEqual(clean, (0.8, 0.6, 0.4))
        self.assertEqual(dusty, (0.1, 0.2, 0.3))


class Annotations(unittest.TestCase):

    def setUp(self):
        self.base = load_family().member("quadv5.fx")

    def test_srgb_is_stated_not_guessed(self):
        # Only the albedo map is sRGB among authored textures. Getting this
        # wrong on a normal map is invisible until lighting looks flat.
        self.assertTrue(self.base.annotation("AlbedoMap").srgb)
        for linear in ("NormalMap", "RoughnessMap", "MaterialMap", "PaintMaskMap",
                       "GlowMap", "DirtMap", "DustNoiseMap"):
            self.assertFalse(self.base.annotation(linear).srgb, linear)

    def test_engine_supplied_resources_are_separated_by_annotation(self):
        # AutoRegister / SasUiVisible=false is Carbon saying "the engine
        # provides this", which is how scene inputs are told from authored maps.
        self.assertIn("EveSpaceSceneShadowMap", self.base.scene_textures)
        self.assertIn("LightBuffer", self.base.scene_textures)
        self.assertNotIn("AlbedoMap", self.base.scene_textures)
        self.assertTrue(self.base.annotation("EveSpaceSceneShadowMap").auto_register)

    def test_dust_uv_scale_matches_the_measured_literal(self):
        # The annotation states 20 and the emitted GLSL multiplies UV by 20.0;
        # two independent statements of one fact.
        self.assertEqual(self.base.annotation("DustNoiseMap").uv_scale, reference.DUST_TILING)
        self.assertEqual(self.base.annotation("AlbedoMap").uv_scale, 1.0)

    def test_general_data_lanes_are_named(self):
        general = self.base.annotation("GeneralData")
        self.assertEqual(general.component(1), "PaintMapInfluence")
        self.assertEqual(general.component(2), "UvSetSelector")

    def test_gloss_names_only_its_first_lane(self):
        # Consistent with only .x being read; the rest is padding.
        gloss = self.base.annotation("Mtl1Gloss")
        self.assertTrue(gloss.component(1))
        self.assertFalse(gloss.component(2))

    def test_widgets_separate_colours_from_scalars(self):
        self.assertTrue(self.base.annotation("Mtl1DiffuseColor").is_color)
        self.assertFalse(self.base.annotation("Mtl1Gloss").is_color)

    def test_groups_give_the_panel_structure(self):
        groups = self.base.groups()
        self.assertIn("Material 1", groups)
        self.assertIn("General", groups)
        for name in ("Mtl1DiffuseColor", "Mtl1FresnelColor", "Mtl1Gloss",
                     "Mtl1DustDiffuseColor"):
            self.assertIn(name, groups["Material 1"])

    def test_paint_mask_declares_transparency(self):
        self.assertTrue(self.base.annotation("PaintMaskMap").has_transparency)


class DustMaterial(unittest.TestCase):

    def test_albedo_is_modulated_by_noise_x(self):
        plain = reference.dusty_albedo((0.5, 0.5, 0.5), (1.0, 1.0, 1.0), 1.0)
        noisy = reference.dusty_albedo((0.5, 0.5, 0.5), (1.0, 1.0, 1.0), 0.5)
        self.assertAlmostEqual(plain[0], 0.5, places=6)
        self.assertAlmostEqual(noisy[0], 0.25, places=6)

    def test_white_dust_colour_leaves_the_albedo_untinted(self):
        # Carbon's default is white, which is why a fully dirty hull on bare
        # defaults shows the plain albedo and reads as clean.
        albedo = (0.2, 0.4, 0.6)
        self.assertEqual(reference.dusty_albedo(albedo, (1.0, 1.0, 1.0, 1.0), 1.0), albedo)

    def test_dust_f0_is_its_own_baked_colour(self):
        # Not derived from the material's fresnel colour at all.
        self.assertEqual(reference.dusty_fresnel(1.0), reference.DIRT_FRESNEL_COLOR)
        self.assertEqual(reference.dusty_fresnel(0.0), (0.0, 0.0, 0.0))

    def test_dust_f0_is_much_darker_than_the_paint_dielectric(self):
        for dust, paint in zip(reference.DIRT_FRESNEL_COLOR, reference.PAINT_FRESNEL_COLOR):
            self.assertLess(dust, paint)

    def test_dusty_roughness_uses_the_baked_gloss(self):
        # Ignores the blended material gloss and the paint mask entirely.
        value = reference.dusty_roughness(1.0, 1.0)
        expected = (1.0 - reference.DIRT_GLOSS) ** 2
        self.assertAlmostEqual(value, expected, places=6)

    def test_dusty_roughness_is_rougher_without_the_map(self):
        self.assertAlmostEqual(reference.dusty_roughness(0.0, 1.0), 1.0, places=6)


class DirtBlendCurve(unittest.TestCase):

    def test_authored_weights_do_not_sum_to_one(self):
        clean, dusty = reference.dirt_weights(0.5)
        self.assertAlmostEqual(clean, 0.125, places=6)
        self.assertAlmostEqual(dusty, 0.5, places=6)

    def test_a_half_mask_is_eighty_percent_dusty(self):
        # The raw mask as a mix factor gives 50% and makes dirt far too weak.
        self.assertAlmostEqual(reference.dirt_blend_factor(0.5), 0.8, places=6)

    def test_the_curve_is_dirtier_than_the_mask_everywhere_between(self):
        for step in range(1, 100):
            mask = step / 100.0
            self.assertGreater(reference.dirt_blend_factor(mask), mask, f"mask={mask}")

    def test_the_ends_still_pin(self):
        self.assertAlmostEqual(reference.dirt_blend_factor(0.0), 0.0, places=6)
        self.assertAlmostEqual(reference.dirt_blend_factor(1.0), 1.0, places=6)

    def test_energy_dips_in_the_middle_and_returns_at_the_ends(self):
        self.assertAlmostEqual(reference.dirt_energy(0.0), 1.0, places=6)
        self.assertAlmostEqual(reference.dirt_energy(1.0), 1.0, places=6)
        self.assertAlmostEqual(reference.dirt_energy(0.5), 0.625, places=6)

    def test_the_split_reproduces_the_authored_combine(self):
        # factor + energy together must equal the production weighting wherever
        # the lighting is linear in the quantity.
        for step in range(0, 101):
            mask = step / 100.0
            direct = reference.combine_dirt((0.8,), (0.2,), mask)[0]
            factor = reference.dirt_blend_factor(mask)
            split = (0.8 + (0.2 - 0.8) * factor) * reference.dirt_energy(mask)
            self.assertAlmostEqual(direct, split, places=6, msg=f"mask={mask}")


class SocketNaming(unittest.TestCase):

    def test_general_data_is_exposed_as_paint_mask_influence(self):
        # Only .x is read, and Carbon's annotation names that lane. The spelling
        # matches sof_shading.GROUP_INPUT_DEFAULTS so existing wiring drives it.
        from carbon_eve_resources.quad import socket_name
        self.assertEqual(socket_name("GeneralData"), "PaintMapInfluence")

    def test_carbon_names_the_same_lane(self):
        base = load_family().member("quadv5.fx")
        self.assertEqual(base.annotation("GeneralData").component(1), "PaintMapInfluence")

    def test_everything_else_keeps_its_carbon_name(self):
        from carbon_eve_resources.quad import socket_name
        base = load_family().member("quadv5.fx")
        for name in base.constants:
            if name == "GeneralData":
                continue
            self.assertEqual(socket_name(name), name)


class PatternWrap(unittest.TestCase):

    def test_projection_types_convert_to_address_modes(self):
        # EveSOFDataPatternLayer.ToAddressMode: 0->1, 1->3, 2->4.
        self.assertEqual(reference.PROJECTION_TO_WRAP[0], reference.WRAP_REPEAT)
        self.assertEqual(reference.PROJECTION_TO_WRAP[1], reference.WRAP_EDGE)
        self.assertEqual(reference.PROJECTION_TO_WRAP[2], reference.WRAP_BORDER)

    def test_repeat_tiles(self):
        for value, expected in ((0.25, 0.25), (1.25, 0.25), (2.5, 0.5)):
            self.assertAlmostEqual(
                reference.wrap_coordinate(value, reference.WRAP_REPEAT), expected, places=6)

    def test_both_clamping_modes_pin_the_lookup_to_the_edge(self):
        # They differ outside the range, not in where they sample.
        for mode in (reference.WRAP_EDGE, reference.WRAP_BORDER):
            self.assertAlmostEqual(reference.wrap_coordinate(1.7, mode), 1.0, places=6)
            self.assertAlmostEqual(reference.wrap_coordinate(-0.3, mode), 0.0, places=6)

    def test_only_border_stops_covering(self):
        R, E, B = reference.WRAP_REPEAT, reference.WRAP_EDGE, reference.WRAP_BORDER
        self.assertEqual(reference.pattern_coverage(1.7, 0.5, R, R), 1.0)
        self.assertEqual(reference.pattern_coverage(1.7, 0.5, E, E), 1.0)
        self.assertEqual(reference.pattern_coverage(1.7, 0.5, B, B), 0.0)

    def test_the_axes_are_independent(self):
        R, B = reference.WRAP_REPEAT, reference.WRAP_BORDER
        # Outside in V only, with V bordered and U repeating.
        self.assertEqual(reference.pattern_coverage(1.7, 1.7, R, B), 0.0)
        self.assertEqual(reference.pattern_coverage(1.7, 0.5, R, B), 1.0)
        self.assertEqual(reference.pattern_coverage(1.7, 1.7, B, R), 0.0)

    def test_inside_the_range_every_combination_covers(self):
        modes = (reference.WRAP_REPEAT, reference.WRAP_EDGE, reference.WRAP_BORDER)
        for mode_u in modes:
            for mode_v in modes:
                self.assertEqual(
                    reference.pattern_coverage(0.5, 0.5, mode_u, mode_v), 1.0)


class AnnotationExpressions(unittest.TestCase):

    def test_uv_scale_can_be_an_authored_expression(self):
        # quadheatv5's HeatGlowNoiseMap declares LodUvScale0 as
        # min(Mtl1HeatGlowData.z, ... ), not a number.
        heat = load_family().members["quadheatv5"]
        annotation = heat.annotation("HeatGlowNoiseMap")
        self.assertIn("min(", annotation.uv_scale_expression)
        # ... and the numeric accessor stays usable rather than throwing.
        self.assertEqual(annotation.uv_scale, 1.0)

    def test_numeric_scales_report_no_expression(self):
        base = load_family().member("quadv5.fx")
        dust = base.annotation("DustNoiseMap")
        self.assertEqual(dust.uv_scale, reference.DUST_TILING)
        self.assertEqual(dust.uv_scale_expression, "")


class Sails(unittest.TestCase):

    def _sampler(self, value):
        return lambda uv: value

    def test_the_sail_texture_reselects_where_layer_one_is_chosen(self):
        # At MaterialMap 0 layer 1 is at full weight, so the sail texture
        # takes over the selector completely.
        selector = reference.sails_selector(
            (0.0, 0.0), 0.0, self._sampler(1.0), (55.0, 0.0))
        self.assertAlmostEqual(selector, 1.0, places=5)

    def test_it_does_nothing_where_another_layer_is_chosen(self):
        # At MaterialMap 1 layer 4 is chosen and layer 1's weight is zero, so
        # the selector is unchanged however bright the sail texture is.
        selector = reference.sails_selector(
            (0.0, 0.0), 1.0, self._sampler(1.0), (55.0, 0.0))
        self.assertAlmostEqual(selector, 1.0, places=5)
        selector = reference.sails_selector(
            (0.0, 0.0), 0.7, self._sampler(1.0), (55.0, 0.0))
        self.assertAlmostEqual(selector, 0.7, places=5)

    def test_tiling_scales_the_lookup(self):
        seen = []
        reference.sails_selector((0.5, 0.25), 0.0,
                                 lambda uv: seen.append(uv) or 0.0, (55.0, 0.0))
        self.assertAlmostEqual(seen[0][0], 27.5, places=4)
        self.assertAlmostEqual(seen[0][1], 13.75, places=4)

    def test_rotation_turns_the_lookup(self):
        import math as _math
        seen = []
        reference.sails_selector((1.0, 0.0), 0.0,
                                 lambda uv: seen.append(uv) or 0.0,
                                 (1.0, _math.pi / 2))
        self.assertAlmostEqual(seen[0][0], 0.0, places=5)
        self.assertAlmostEqual(seen[0][1], 1.0, places=5)

    def test_the_two_sail_areas_differ_only_by_rotation(self):
        # A Legion's two area_sails share every value but SailsDetailData.y,
        # which is why one node group serves both.
        flat = reference.sails_selector((0.3, 0.4), 0.0, self._sampler(0.5), (55.0, 0.0))
        turned = reference.sails_selector((0.3, 0.4), 0.0, self._sampler(0.5), (55.0, 1.6))
        self.assertAlmostEqual(flat, turned, places=6)


class DecalIndexBuffers(unittest.TestCase):

    def setUp(self):
        from carbon_eve_resources.quad import decals
        self.decals = decals

    def test_the_buffers_are_alternatives_not_parts(self):
        # A Legion decal's seven buffers run 48, 45, 39, ... -- a LOD chain.
        # Taking them all draws every level at once, which scatters stray faces
        # across the hull because the coarser ones index different triangles.
        buffers = [[0, 1, 2, 3, 4, 5], [6, 7, 8]]
        self.assertEqual(len(self.decals.triangles_from_buffers(buffers)), 2)

    def test_level_zero_is_the_most_detailed(self):
        buffers = [[0, 1, 2, 3, 4, 5], [6, 7, 8]]
        self.assertEqual(self.decals.triangles_from_buffers(buffers, lod=0),
                         ((0, 1, 2), (3, 4, 5)))
        self.assertEqual(self.decals.triangles_from_buffers(buffers, lod=1),
                         ((6, 7, 8),))

    def test_a_missing_level_falls_back_to_the_coarsest(self):
        buffers = [[0, 1, 2], [3, 4, 5]]
        self.assertEqual(self.decals.triangles_from_buffers(buffers, lod=9),
                         ((3, 4, 5),))

    def test_no_buffers_is_no_triangles(self):
        self.assertEqual(self.decals.triangles_from_buffers(None), ())
        self.assertEqual(self.decals.triangles_from_buffers([]), ())

    def test_a_trailing_partial_triangle_is_dropped(self):
        self.assertEqual(self.decals.triangles_from_buffers([[0, 1, 2, 3]]), ((0, 1, 2),))


class HeatGlow(unittest.TestCase):

    def test_the_gate_is_a_narrow_window_just_above_zero(self):
        # Fully on by a booster gain of 0.02, which is why heat reads as a
        # switch rather than a fade.
        self.assertAlmostEqual(reference.heat_gate(0.0, 1.0), 0.0, places=5)
        self.assertAlmostEqual(reference.heat_gate(0.02, 1.0), 1.0, places=4)

    def test_influence_zero_ignores_the_boosters(self):
        # A material with HeatGlowData.x of zero always glows.
        self.assertAlmostEqual(reference.heat_gate(0.0, 0.0), 1.0, places=6)

    def test_influence_one_follows_the_boosters(self):
        self.assertAlmostEqual(reference.heat_gate(0.0, 1.0), 0.0, places=6)
        self.assertAlmostEqual(reference.heat_gate(1.0, 1.0), 1.0, places=6)

    def test_the_two_noise_taps_scroll_opposite_ways(self):
        seen = []
        reference.heat_offset((0.0, 0.0), 1.0, (0.0, 2.0, 1.0, 1.0), 1.0,
                              lambda uv: seen.append(uv) or (1.0, 1.0))
        self.assertAlmostEqual(seen[0][0], 2.0, places=5)
        self.assertAlmostEqual(seen[1][0], -2.0, places=5)

    def test_average_noise_leaves_the_glow_where_it_is(self):
        # The product is centred on 0.5, so noise of sqrt(0.5) displaces nothing.
        root = 0.5 ** 0.5
        offset = reference.heat_offset((0.3, 0.4), 1.0, (0.0, 1.0, 1.0, 1.0), 1.0,
                                       lambda uv: (root, root))
        self.assertAlmostEqual(offset[0], 0.0, places=6)

    def test_no_heat_means_no_displacement(self):
        offset = reference.heat_offset((0.3, 0.4), 5.0, (1.0, 1.0, 1.0, 1.0), 0.0,
                                       lambda uv: (1.0, 1.0))
        self.assertEqual(offset, (0.0, 0.0))

    def test_heat_reuses_the_glow_map(self):
        # A hull with no glow detail shows no heat however hot it is.
        dark = reference.heat_emissive(lambda uv: 0.0, (0, 0), (0, 0), (1, 0.5, 0), 1.0, 1.0)
        self.assertEqual(dark, (0.0, 0.0, 0.0))

    def test_heat_uses_the_same_exponent_as_the_base_glow(self):
        value = reference.heat_emissive(lambda uv: 0.5, (0, 0), (0, 0), (1, 1, 1), 1.0, 1.0)
        self.assertAlmostEqual(value[0], pow(0.5, 2.4), places=6)


class KillCounterTests(unittest.TestCase):
    """`decalcounterv5` draws tally marks, not digits."""

    def test_digits_split_into_units_tens_hundreds(self):
        for count, expected in ((0, [0, 0, 0]), (7, [7, 0, 0]), (10, [0, 1, 0]),
                                (27, [7, 2, 0]), (105, [5, 0, 1]), (999, [9, 9, 9])):
            rows = [int(reference.kill_counter_digit(count, row)) for row in (0, 1, 2)]
            self.assertEqual(rows, expected, f"count {count}")

    def test_a_row_lights_one_mark_per_unit(self):
        # The bottom row spans v in [0, 1/3); nine columns across u.
        lit = [reference.kill_counter_coverage(((column + 0.5) / 9.0, 0.1), 3)
               for column in range(9)]
        self.assertEqual(lit, [1.0, 1.0, 1.0] + [0.0] * 6)

    def test_nothing_is_drawn_outside_the_decal(self):
        for uv in ((-0.1, 0.5), (1.1, 0.5), (0.5, -0.1), (0.5, 1.1)):
            self.assertEqual(reference.kill_counter_coverage(uv, 9), 0.0, uv)

    def test_the_mark_texture_repeats_nine_times_across(self):
        self.assertEqual(reference.kill_counter_mark_uv((1.0, 1.0)), (9.0, 1.0))

    def test_alpha_squares_after_scaling(self):
        self.assertAlmostEqual(reference.kill_counter_alpha(0.5, 2.0, 1.0), 1.0)
        self.assertEqual(reference.kill_counter_alpha(0.5, 2.0, 0.0), 0.0)


class HullBreachTests(unittest.TestCase):
    """`decalholev5` fakes interior depth with a unit sphere."""

    def test_looking_straight_in_exits_straight_ahead(self):
        got = reference.hole_interior_direction((0.0, 0.0, -0.5), (0.0, 0.0, 1.0))
        self.assertEqual(tuple(round(x, 6) for x in got), (0.0, 0.0, 1.0))

    def test_the_exit_point_lies_on_the_unit_sphere(self):
        for point, view in (((0.0, 0.0, -0.5), (0.0, 0.0, 1.0)),
                            ((0.4, -0.2, 0.1), (0.3, 0.5, 1.0)),
                            ((0.9, 0.0, 0.0), (0.0, 0.0, 1.0))):
            got = reference.hole_interior_direction(point, view)
            self.assertIsNotNone(got, point)
            length = sum(axis * axis for axis in got) ** 0.5
            self.assertAlmostEqual(length, 1.0, places=6)

    def test_a_ray_that_misses_the_sphere_is_discarded(self):
        self.assertIsNone(reference.hole_interior_direction((3.0, 0.0, 0.0), (0.0, 1.0, 0.0)))

    def test_the_view_direction_moves_the_interior(self):
        # The parallax IS the effect: two angles must not sample the same place.
        straight = reference.hole_interior_direction((0.0, 0.0, -0.5), (0.0, 0.0, 1.0))
        oblique = reference.hole_interior_direction((0.0, 0.0, -0.5), (0.6, 0.0, 1.0))
        self.assertNotEqual(tuple(round(x, 4) for x in straight),
                            tuple(round(x, 4) for x in oblique))

    def test_the_rim_blends_into_the_interior(self):
        glow = (1.0, 0.5, 0.0)
        rim_only = reference.hole_colour(1.0, 0.0, 0.0, glow)
        interior_only = reference.hole_colour(0.0, 1.0, 1.0, glow)
        self.assertEqual(tuple(round(x, 6) for x in rim_only), (1.0, 0.5, 0.0))
        self.assertEqual(tuple(round(x, 6) for x in interior_only), (1.0, 0.5, 0.0))
        self.assertEqual(reference.hole_colour(0.0, 0.5, 0.0, glow), (0.0, 0.0, 0.0))


class DecalNamingTests(unittest.TestCase):
    """A built decal carries neither its name nor its visibility group."""

    def _decal(self, index, shader, rotation, scaling, bone):
        return decals.Decal(index=index, shader=shader, position=(0.0, 0.0, 0.0),
                            rotation=rotation, scaling=scaling, parent_bone=bone,
                            triangles=(), textures={}, constants={})

    def _set(self, name, group, items):
        return {"name": name, "visibilityGroup": group, "items": items}

    def test_a_decal_takes_its_set_name_and_visibility_group(self):
        built = [self._decal(0, "decalv5.fx", (0, 0, 0, 1), (1, 1, 1), -1)]
        sets = [self._set("hull_damage", "damage", [
            {"name": "scorch_01", "usage": 0, "rotation": (0, 0, 0, 1),
             "scaling": (1, 1, 1), "boneIndex": -1}])]
        named = decals.name_decals(built, sets)
        self.assertEqual(named[0].sof_name, "scorch_01")
        self.assertEqual(named[0].visibility_group, "damage")
        self.assertEqual(named[0].group, "hull_damage")

    def test_position_is_not_part_of_the_key(self):
        # A strategic cruiser offsets decal positions per subsystem hull, so a
        # match must survive the position disagreeing.
        built = [self._decal(0, "decalv5.fx", (0, 0, 0, 1), (1, 1, 1), 4)]
        built[0] = decals.Decal(index=0, shader="decalv5.fx",
                                position=(900.0, -12.0, 5.0),
                                rotation=(0, 0, 0, 1), scaling=(1, 1, 1),
                                parent_bone=4, triangles=(), textures={}, constants={})
        sets = [self._set("wing", "primary", [
            {"name": "wing_mark", "usage": 0, "position": (0, 0, 0),
             "rotation": (0, 0, 0, 1), "scaling": (1, 1, 1), "boneIndex": 4}])]
        self.assertEqual(decals.name_decals(built, sets)[0].sof_name, "wing_mark")

    def test_skipped_sets_do_not_shift_the_names(self):
        # The builder drops invisible sets, so the lists differ in length and an
        # index mapping would name this decal from the wrong set.
        built = [self._decal(0, "decalholev5.fx", (0, 0, 1, 0), (2, 2, 2), -1)]
        sets = [
            self._set("never_built", "hidden", [
                {"name": "ghost", "usage": 0, "rotation": (0, 0, 0, 1),
                 "scaling": (1, 1, 1), "boneIndex": -1}]),
            self._set("breaches", "damage", [
                {"name": "breach_a", "usage": 2, "rotation": (0, 0, 1, 0),
                 "scaling": (2, 2, 2), "boneIndex": -1}]),
        ]
        named = decals.name_decals(built, sets)
        self.assertEqual(named[0].sof_name, "breach_a")
        self.assertEqual(named[0].group, "breaches")

    def test_two_decals_sharing_a_transform_take_one_candidate_each(self):
        built = [self._decal(0, "decalv5.fx", (0, 0, 0, 1), (1, 1, 1), -1),
                 self._decal(1, "decalv5.fx", (0, 0, 0, 1), (1, 1, 1), -1)]
        sets = [self._set("pair", "primary", [
            {"name": "left", "usage": 0, "rotation": (0, 0, 0, 1),
             "scaling": (1, 1, 1), "boneIndex": -1},
            {"name": "right", "usage": 0, "rotation": (0, 0, 0, 1),
             "scaling": (1, 1, 1), "boneIndex": -1}])]
        named = decals.name_decals(built, sets)
        self.assertEqual([d.sof_name for d in named], ["left", "right"])

    def test_an_unmatched_decal_is_left_alone(self):
        built = [self._decal(0, "decalglowv5.fx", (0, 0, 0, 1), (1, 1, 1), -1)]
        named = decals.name_decals(built, [])
        self.assertEqual(named[0].sof_name, "")
        self.assertEqual(named[0].group, "decalglowv5")

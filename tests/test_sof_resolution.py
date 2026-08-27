from pathlib import Path
import sys
import unittest


ADDONS = Path(__file__).resolve().parents[1] / "addons"
if str(ADDONS) not in sys.path:
    sys.path.insert(0, str(ADDONS))

from carbon_eve_resources.sof_resolution import (  # noqa: E402
    NONE,
    SOURCE_DNA,
    SOURCE_FACTION,
    DnaError,
    compose,
    parse,
    slot_sources,
    with_materials,
    with_pattern,
)


PLAIN = "mde3_t3:minmatarbase:minmatar"
SKINNED = ("mde3_t3:minmatarbase:minmatar"
           ":material?mtl_a;mtl_b;none;none"
           ":pattern?pattern_x;pmtl_a;pmtl_b")


class ParseTests(unittest.TestCase):
    def test_reads_the_three_components(self):
        dna = parse(PLAIN)
        self.assertEqual(dna.hull, "mde3_t3")
        self.assertEqual(dna.faction, "minmatarbase")
        self.assertEqual(dna.race, "minmatar")
        self.assertEqual(dna.commands, {})

    def test_lowercases_like_the_runtime(self):
        # A DNA authored `MATERIAL?...` is real; a case-sensitive reader drops
        # its materials and silently paints the ship in faction colours.
        dna = parse("MDE3_T3:MinmatarBase:Minmatar:MATERIAL?Mtl_A;none;none;none")
        self.assertEqual(dna.hull, "mde3_t3")
        self.assertEqual(dna.materials[0], "mtl_a")

    def test_mesh_is_material(self):
        dna = parse(f"{PLAIN}:mesh?mtl_a;mtl_b;none;none")
        self.assertEqual(dna.materials, ("mtl_a", "mtl_b", NONE, NONE))

    def test_an_explicit_material_beats_the_mesh_alias(self):
        dna = parse(f"{PLAIN}:material?real;none;none;none:mesh?alias;none;none;none")
        self.assertEqual(dna.materials[0], "real")

    def test_a_short_material_command_pads_rather_than_shifts(self):
        dna = parse(f"{PLAIN}:material?only_one")
        self.assertEqual(dna.materials, ("only_one", NONE, NONE, NONE))

    def test_several_hulls(self):
        dna = parse("asc1_t3;msc1:minmatarbase:minmatar")
        self.assertEqual(dna.hulls, ("asc1_t3", "msc1"))
        self.assertEqual(dna.hull, "asc1_t3")

    def test_rejects_a_short_dna(self):
        with self.assertRaises(DnaError):
            parse("mde3_t3:minmatarbase")

    def test_rejects_a_command_with_no_payload(self):
        with self.assertRaises(DnaError):
            parse(f"{PLAIN}:material")


class ComposeTests(unittest.TestCase):
    def test_round_trips(self):
        self.assertEqual(parse(SKINNED).compose(), SKINNED)

    def test_sorts_commands_so_the_same_ship_is_the_same_text(self):
        # `EveSOFDNA` sorts when it composes. A DNA that differs only in
        # command order is the same ship, and must not read as two.
        one = parse(f"{PLAIN}:pattern?p;a;b:material?m;none;none;none").compose()
        two = parse(f"{PLAIN}:material?m;none;none;none:pattern?p;a;b").compose()
        self.assertEqual(one, two)

    def test_needs_a_hull(self):
        with self.assertRaises(DnaError):
            compose("", "minmatarbase", "minmatar")


class MaterialEditTests(unittest.TestCase):
    def test_replaces_the_slots(self):
        edited = with_materials(SKINNED, ["new_a", NONE, NONE, NONE])
        self.assertEqual(parse(edited).materials, ("new_a", NONE, NONE, NONE))

    def test_keeps_the_other_commands(self):
        edited = parse(with_materials(SKINNED, ["new_a", NONE, NONE, NONE]))
        self.assertEqual(edited.pattern, ("pattern_x", "pmtl_a", "pmtl_b"))

    def test_all_none_drops_the_command_entirely(self):
        # Every slot `none` IS a ship with no overrides, which is a DNA with no
        # material command -- not one that spells out four absences.
        edited = with_materials(SKINNED, [NONE] * 4)
        self.assertNotIn("material?", edited)
        self.assertEqual(parse(edited).materials, (NONE,) * 4)

    def test_writes_material_even_when_the_dna_said_mesh(self):
        edited = with_materials(f"{PLAIN}:mesh?a;b;c;d", ["x", "y", "z", "w"])
        self.assertIn("material?x;y;z;w", edited)
        self.assertNotIn("mesh?", edited)


class PatternEditTests(unittest.TestCase):
    def test_sets_the_pattern_and_its_layers(self):
        edited = parse(with_pattern(PLAIN, "pattern_x", ["pmtl_a", "pmtl_b"]))
        self.assertEqual(edited.pattern, ("pattern_x", "pmtl_a", "pmtl_b"))

    def test_an_empty_pattern_removes_it(self):
        self.assertNotIn("pattern?", with_pattern(SKINNED, ""))

    def test_missing_layers_become_none(self):
        edited = parse(with_pattern(PLAIN, "pattern_x"))
        self.assertEqual(edited.pattern, ("pattern_x", NONE, NONE))


class SlotSourceTests(unittest.TestCase):
    def test_a_named_material_is_an_override(self):
        sources = {s.index: s for s in slot_sources(SKINNED) if not s.is_pattern}
        self.assertEqual(sources[1].source, SOURCE_DNA)
        self.assertEqual(sources[1].material, "mtl_a")

    def test_none_falls_through_to_the_faction(self):
        sources = {s.index: s for s in slot_sources(SKINNED) if not s.is_pattern}
        self.assertEqual(sources[3].source, SOURCE_FACTION)
        self.assertEqual(sources[3].material, "")

    def test_a_dna_with_no_material_command_is_all_faction(self):
        sources = [s for s in slot_sources(PLAIN) if not s.is_pattern]
        self.assertEqual([s.source for s in sources], [SOURCE_FACTION] * 4)

    def test_pattern_layers_are_always_from_the_dna(self):
        # There is no faction fallback for a pattern, which is how a SKIN
        # repaints a hull whose faction says nothing about it.
        patterns = [s for s in slot_sources(SKINNED) if s.is_pattern]
        self.assertEqual([s.source for s in patterns], [SOURCE_DNA, SOURCE_DNA])
        self.assertEqual([s.material for s in patterns], ["pmtl_a", "pmtl_b"])

    def test_every_slot_is_reported(self):
        self.assertEqual(len(slot_sources(PLAIN)), 6)


if __name__ == "__main__":
    unittest.main()

"""The SOF editor edits the DNA's COMMANDS, and must not lose any of them."""

from pathlib import Path
import sys
import unittest


ADDONS = Path(__file__).resolve().parents[1] / "addons"
if str(ADDONS) not in sys.path:
    sys.path.insert(0, str(ADDONS))

try:
    import bpy
except ImportError:                      # pragma: no cover - outside Blender
    bpy = None

if bpy is not None:
    from carbon_eve_resources import sof_panels
    from carbon_eve_resources.core import sof_resolution


FULL = ("mde3_t3:legion_minmatar:minmatar"
        ":material?mtl_a;none;mtl_c;none"
        ":pattern?legion_minmatar;brown_dust_coated;orange_fire_colorshift"
        ":respathinsert?deathless"
        ":layout?upwell_hangar;small_docks")


@unittest.skipIf(bpy is None, "needs Blender")
class EditorTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        try:
            sof_panels.register()
        except Exception:
            pass
        self.obj = bpy.data.objects.new("ship", None)
        bpy.context.scene.collection.objects.link(self.obj)
        self.settings = self.obj.carbon_sof

    def test_reads_the_three_compulsory_components(self):
        self.settings.read_dna(FULL)
        self.assertEqual((self.settings.hull, self.settings.faction, self.settings.race),
                         ("mde3_t3", "legion_minmatar", "minmatar"))

    def test_reads_all_four_mesh_materials_including_the_nones(self):
        self.settings.read_dna(FULL)
        self.assertTrue(self.settings.use_mesh)
        self.assertEqual(
            [getattr(self.settings, f"mesh_material{i}") for i in (1, 2, 3, 4)],
            ["mtl_a", "none", "mtl_c", "none"])

    def test_reads_the_pattern_and_its_two_layer_materials(self):
        self.settings.read_dna(FULL)
        self.assertTrue(self.settings.use_pattern)
        self.assertEqual(self.settings.pattern, "legion_minmatar")
        self.assertEqual(self.settings.pattern_material5, "brown_dust_coated")
        self.assertEqual(self.settings.pattern_material6, "orange_fire_colorshift")

    def test_reads_respathinsert_and_layouts(self):
        self.settings.read_dna(FULL)
        self.assertEqual(self.settings.respath_insert, "deathless")
        # Several layouts are legal; GetLayoutData takes a list.
        self.assertEqual(self.settings.layout_names, "upwell_hangar;small_docks")

    def test_round_trips_to_the_runtime_canonical_form(self):
        # Not to the input text: EveSOFDNA SORTS its commands when it composes,
        # so a DNA differing only in command order is the same ship.
        self.settings.read_dna(FULL)
        self.assertEqual(self.settings.compose_dna(),
                         sof_resolution.parse(FULL).compose())

    def test_the_optional_commands_are_absent_when_off(self):
        self.settings.read_dna(FULL)
        self.settings.use_layout = False
        self.settings.use_respath = False
        made = self.settings.compose_dna()
        self.assertNotIn("layout?", made)
        self.assertNotIn("respathinsert?", made)
        self.assertIn("pattern?", made)

    def test_a_mesh_command_of_all_nones_is_no_command(self):
        # `none` is an absence, so four of them is a ship with no overrides --
        # which is a DNA with no material command, not one spelling out four.
        self.settings.read_dna("mde3_t3:legion_minmatar:minmatar")
        self.settings.use_mesh = True
        self.assertNotIn("material?", self.settings.compose_dna())

    def test_a_plain_dna_switches_every_optional_command_off(self):
        self.settings.read_dna(FULL)
        self.settings.read_dna("mde3_t3:legion_minmatar:minmatar")
        for toggle in ("use_mesh", "use_pattern", "use_respath", "use_layout"):
            self.assertFalse(getattr(self.settings, toggle), toggle)

    def test_an_unreadable_dna_changes_nothing(self):
        self.settings.read_dna(FULL)
        self.assertFalse(self.settings.read_dna("not-a-dna"))
        self.assertEqual(self.settings.hull, "mde3_t3")

    def test_choosing_parts_writes_the_dna(self):
        self.settings.dna = "mde3_t3:legion_minmatar:minmatar"
        self.settings.use_mesh = True
        self.settings.mesh_material1 = "mtl_a"
        self.assertIn("material?mtl_a;none;none;none", self.settings.dna)

    def test_a_command_switched_on_but_empty_stays_on(self):
        # It did not: composing an empty command produced a DNA without it, and
        # parsing that DNA switched it straight back off -- so turning Mesh on
        # and then naming a material could not work at all.
        self.settings.dna = "mde3_t3:legion_minmatar:minmatar"
        self.settings.use_mesh = True
        self.assertTrue(self.settings.use_mesh)
        self.assertNotIn("material?", self.settings.dna)

    def test_typing_a_dna_populates_the_parts(self):
        self.settings.dna = FULL
        self.assertEqual(self.settings.hull, "mde3_t3")
        self.assertTrue(self.settings.use_pattern)
        self.assertEqual(self.settings.pattern_material5, "brown_dust_coated")

    def test_a_typed_dna_is_not_recomposed_over(self):
        # The text a person typed must survive being parsed: a recompose is
        # canonical, so a DNA carrying a command the editor does not model yet
        # would be quietly rewritten without it.
        self.settings.dna = FULL
        self.assertEqual(self.settings.dna, FULL)

    def test_switching_a_command_off_removes_it_from_the_dna(self):
        self.settings.dna = FULL
        self.settings.use_respath = False
        self.assertNotIn("respathinsert?", self.settings.dna)

    def test_a_typed_dna_is_stored_lower_case(self):
        # A DNA is case-insensitive and written in lower case. `MDE3_T3` is the
        # same ship wearing a different spelling, and storing it that way meant
        # the field said one thing while the runtime read another.
        self.settings.dna = "MDE3_T3:Legion_Minmatar:Minmatar"
        self.assertEqual(self.settings.dna, "mde3_t3:legion_minmatar:minmatar")

    def test_a_typed_name_field_is_lower_cased(self):
        # It is handed to a case-sensitive route, so the case is not cosmetic.
        self.settings.dna = "mde3_t3:legion_minmatar:minmatar"
        self.settings.use_mesh = True
        self.settings.mesh_material1 = "Black_DeadStar_Coated"
        self.assertEqual(self.settings.mesh_material1, "black_deadstar_coated")

    def test_the_dna_is_kept_verbatim_on_the_way_in(self):
        # Recomposing on read would silently drop anything the editor does not
        # model yet. Verbatim apart from the case, which is normalised.
        self.settings.read_dna(FULL)
        self.assertEqual(self.settings.dna, FULL.lower())


if __name__ == "__main__":
    unittest.main()


class CatalogRetryTests(unittest.TestCase):
    """A dropdown that vanished for the session because one fetch failed.

    The kind is marked as requested BEFORE the fetch. Without a retry, a cold
    start or a slow service left the ship field as a plain text box for the
    rest of the session -- with nothing on screen to say why.
    """

    def source(self):
        from pathlib import Path

        here = Path(__file__).resolve().parents[1]
        return (here / "addons" / "carbon_eve_resources"
                / "sof_panels.py").read_text(encoding="utf-8")

    def test_an_empty_catalog_is_forgotten_so_it_is_asked_for_again(self):
        self.assertIn("_REQUESTED.discard(kind)", self.source())

    def test_the_retry_is_capped(self):
        # Or a service that is genuinely down is asked on every redraw,
        # forever.
        text = self.source()
        self.assertIn("MAX_ATTEMPTS", text)
        self.assertIn("_ATTEMPTS[kind] < MAX_ATTEMPTS", text)

    def test_forgetting_the_catalogs_clears_the_attempts_too(self):
        # Otherwise a build change cannot revive a catalog that gave up.
        text = self.source()
        self.assertIn("_ATTEMPTS.clear()", text)

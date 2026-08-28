"""Name -> type/skin -> DNA, against the shapes tools-core actually serves."""

from pathlib import Path
import sys
import unittest


ADDONS = Path(__file__).resolve().parents[1] / "addons"
if str(ADDONS) not in sys.path:
    sys.path.insert(0, str(ADDONS))

from carbon_eve_resources.core import sof_lookup  # noqa: E402


#: Verbatim shapes from the live routes.
NAMES = {
    "tengu": [{"graphicID": 20215, "groupID": 963, "kind": "type",
               "skinID": None, "typeID": 29984}],
    "abaddon amarr exoplanets": [{"kind": "skin", "skinID": 4288, "typeID": 24692}],
}
TYPE = {"typeID": 29984, "graphicID": 20215, "name": {"text": "Tengu", "language": "en"}}
GRAPHIC = {"payload": {"_key": 20215, "sofFactionName": "caldaribase",
                       "sofHullName": "csc1_t3", "sofRaceName": "caldari"}}
SKINS = {"4288": {"skinID": 4288, "skinMaterialID": 185, "types": [24692]},
         "12618": {"skinID": 12618, "skinMaterialID": 900, "types": [33820]}}
SKIN_MATERIALS = {"185": {"skinMaterialID": 185, "materialSetID": 210},
                  "900": {"skinMaterialID": 900, "materialSetID": 3490}}
SKIN_SETS = {"210": {"materialSetID": 210, "material1": "blue_darknavy_enamel",
                     "material2": "grey_darksteel_brushed",
                     "material3": "black_gunmetal_metallic",
                     "material4": "orange_bright_matt",
                     "resPathInsert": "amarr", "sofFactionName": "amarrbase"},
             # Barghest Shattered Paradigm, verbatim: it repaints with a
             # PATTERN and names no materials -- and spells their absence with
             # a capital N.
             "3490": {"materialSetID": 3490,
                      "material1": "None", "material2": "None",
                      "material3": "None", "material4": "None",
                      "patternMaterial1": "orange_fire_colorshift",
                      "patternMaterial2": "white_ghost_enamel",
                      "sofPatternName": "igc_xx_mordu",
                      "sofFactionName": "igc_xx_mordu"}}
ABADDON_GRAPHIC = {"payload": {"sofHullName": "ab3_t1", "sofFactionName": "amarrbase",
                               "sofRaceName": "amarr"}}


class _Client:
    """Answers the routes this module uses, and records what was asked."""

    def __init__(self):
        self.routes = []

    def request_json(self, method, route):
        self.routes.append(route)
        if route.endswith("/skin/names"):
            return NAMES
        if route.endswith("/skin/skins"):
            return SKINS
        if route.endswith("/skin/skinMaterials"):
            return SKIN_MATERIALS
        if route.endswith("/skin/skinMaterialSets"):
            return SKIN_SETS
        if route.endswith("/types/29984"):
            return TYPE
        if route.endswith("/types/24692"):
            return {"typeID": 24692, "graphicID": 33}
        if route.endswith("/types/33820"):
            return {"typeID": 33820, "graphicID": 44}
        if route.endswith("/sde/graphics/20215"):
            return GRAPHIC
        if route.endswith("/sde/graphics/33"):
            return ABADDON_GRAPHIC
        if route.endswith("/sde/graphics/44"):
            return {"payload": {"sofHullName": "morb1_t1",
                                "sofFactionName": "mordu",
                                "sofRaceName": "mordu"}}
        raise RuntimeError(f"no route: {route}")


class LookupTests(unittest.TestCase):
    def setUp(self):
        sof_lookup.forget()
        self.client = _Client()

    def test_a_name_is_matched_however_it_is_typed(self):
        self.assertEqual(sof_lookup.find("Tengu", self.client)[0]["typeID"], 29984)
        self.assertEqual(sof_lookup.find("  tengu ", self.client)[0]["typeID"], 29984)

    def test_a_name_can_be_filtered_by_kind(self):
        # One name can mean a type and a skin at once.
        self.assertEqual(sof_lookup.find("tengu", self.client, kind="skin"), [])
        self.assertEqual(len(sof_lookup.find("tengu", self.client, kind="type")), 1)

    def test_a_type_reaches_its_sof_names_through_its_graphic(self):
        # The type record does not carry them; the graphic does.
        found = sof_lookup.type_components(29984, self.client)
        self.assertEqual(found["hull"], "csc1_t3")
        self.assertEqual(found["faction"], "caldaribase")
        self.assertEqual(found["name"], "Tengu")

    def test_the_sde_payload_wrapper_is_unwrapped(self):
        self.assertEqual(sof_lookup.graphic_record(20215, self.client)["sofHullName"],
                         "csc1_t3")

    def test_a_type_with_no_sof_names_resolves_to_nothing(self):
        # Two thirds of a DNA plus a guess is a DNA naming something that does
        # not exist.
        sof_lookup._CACHE[("eve", "latest", "graphic", 20215)] = {"payload": {}}
        self.assertEqual(sof_lookup.type_components(29984, self.client), {})

    def test_a_type_alone_makes_a_plain_dna(self):
        self.assertEqual(sof_lookup.dna_for(29984, client=self.client),
                         "csc1_t3:caldaribase:caldari")

    def test_a_skin_supplies_materials_faction_and_respath(self):
        made = sof_lookup.dna_for(24692, 4288, self.client)
        # `mesh?`, which is what CjsToolSde.BuildSkinDna emits and what live
        # skins are authored with. The runtime reads it as `material`.
        self.assertIn("mesh?blue_darknavy_enamel;", made)
        self.assertIn("respathinsert?amarr", made)
        self.assertTrue(made.startswith("ab3_t1:amarrbase:amarr"))

    def test_a_skin_belongs_to_its_types(self):
        self.assertTrue(sof_lookup.skin_applies(4288, 24692, self.client))
        # A Rifter wearing an Abaddon's materials is a DNA that resolves and
        # draws something that cannot exist.
        self.assertFalse(sof_lookup.skin_applies(4288, 587, self.client))

    def test_a_skin_can_repaint_with_a_pattern_instead_of_materials(self):
        # Barghest Shattered Paradigm names no materials at all. Reading only
        # the materials gave it a DNA with nothing in it, and it loaded
        # unpainted.
        made = sof_lookup.dna_for(33820, 12618, self.client)
        self.assertIn("pattern?igc_xx_mordu;orange_fire_colorshift;white_ghost_enamel",
                      made)

    def test_a_capital_none_is_still_an_absence(self):
        # These arrive capitalised. A case-sensitive test read four absences as
        # four overrides and wrote a material command that said nothing.
        made = sof_lookup.dna_for(33820, 12618, self.client)
        self.assertNotIn("mesh?", made)
        self.assertNotIn("material?", made)

    def test_pattern_materials_are_read_under_either_spelling(self):
        # Some rows spell them `customMaterial1`. A reader that knows only
        # `patternMaterial1` silently drops those skins' patterns.
        SKIN_SETS["3490"] = dict(SKIN_SETS["3490"])
        SKIN_SETS["3490"].pop("patternMaterial1")
        SKIN_SETS["3490"]["customMaterial1"] = "orange_fire_colorshift"
        sof_lookup.forget()
        self.assertIn("orange_fire_colorshift",
                      sof_lookup.dna_for(33820, 12618, self.client))

    def test_nothing_is_fetched_twice(self):
        sof_lookup.dna_for(29984, client=self.client)
        before = len(self.client.routes)
        sof_lookup.dna_for(29984, client=self.client)
        self.assertEqual(len(self.client.routes), before)

    def test_no_client_is_empty_not_a_crash(self):
        sof_lookup.forget()
        self.assertEqual(sof_lookup.find("tengu"), [])
        self.assertEqual(sof_lookup.dna_for(29984), "")


if __name__ == "__main__":
    unittest.main()

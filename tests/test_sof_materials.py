from pathlib import Path
import sys
import unittest


ADDONS = Path(__file__).resolve().parents[1] / "addons"
if str(ADDONS) not in sys.path:
    sys.path.insert(0, str(ADDONS))

from carbon_eve_resources.core import sof_materials  # noqa: E402


#: legion_minmatar's table, verbatim from the live faction route. The sails
#: name only slot 4; everything else of theirs falls back to primary.
LEGION = {
    "areaMaterials": {
        "materialNames": {
            "0:0": "black_deadstar_coated",
            "0:1": "black_deadstar_matt",
            "0:2": "black_machine_brushed",
            "0:3": "brown_dust_matt",
            "2:3": "sails_minmatar",
        },
    },
}

#: black_deadstar_coated, verbatim. Note Gloss is a vec4 with the value in x.
MATERIAL = {
    "_type": "EveSOFDataMaterial",
    "name": "black_deadstar_coated",
    "parameters": {
        "DiffuseColor": [0.0012141, 0.0012141, 0.0012141, 1],
        "DustDiffuseColor": [0.0159963, 0.012983, 0.012983, 1],
        "FresnelColor": [0.0343398, 0.0343398, 0.0343398, 1],
        "Gloss": [0.7735, 0, 0, 0],
    },
}


class FactionTableTests(unittest.TestCase):
    def test_reads_the_flattened_table(self):
        names = sof_materials.faction_material_names(LEGION)
        self.assertEqual(names["0:0"], "black_deadstar_coated")

    def test_a_missing_record_is_empty_not_an_error(self):
        self.assertEqual(sof_materials.faction_material_names(None), {})
        self.assertEqual(sof_materials.faction_material_names({}), {})


class NameLookupTests(unittest.TestCase):
    def setUp(self):
        self.names = sof_materials.faction_material_names(LEGION)

    def test_names_a_primary_slot(self):
        self.assertEqual(
            sof_materials.material_name_for(self.names, 0, 1),
            "black_deadstar_coated")

    def test_the_sails_own_slot_four(self):
        self.assertEqual(
            sof_materials.material_name_for(self.names, 2, 4), "sails_minmatar")

    def test_the_sails_other_slots_fall_back_to_primary(self):
        # This is why only slot 4 measured differently between the hull and the
        # sails: the sails name nothing else, so they inherit primary's.
        for index in (1, 2, 3):
            self.assertEqual(
                sof_materials.material_name_for(self.names, 2, index),
                sof_materials.material_name_for(self.names, 0, index))

    def test_an_unnamed_primary_slot_stays_empty(self):
        # Primary is the end of the fallback chain; it must not recurse.
        self.assertEqual(sof_materials.material_name_for({}, 0, 1), "")

    def test_an_area_with_nothing_anywhere_stays_empty(self):
        self.assertEqual(sof_materials.material_name_for({}, 2, 1), "")


class MaterialValueTests(unittest.TestCase):
    def test_reads_the_three_fields_a_slot_shows(self):
        values = sof_materials.material_values(MATERIAL)
        self.assertEqual(set(values), {"diffuse", "fresnel", "gloss"})

    def test_gloss_is_the_x_component_not_the_vector(self):
        # Every SOF parameter is a vec4, including the scalars.
        self.assertAlmostEqual(sof_materials.material_values(MATERIAL)["gloss"],
                               0.7735, places=4)

    def test_colours_drop_the_alpha(self):
        diffuse = sof_materials.material_values(MATERIAL)["diffuse"]
        self.assertEqual(len(diffuse), 3)

    def test_extra_parameters_are_left_alone(self):
        # A material carries more than a slot shows -- DustDiffuseColor here --
        # and inventing a home for it would be worse than ignoring it.
        self.assertNotIn("DustDiffuseColor", sof_materials.material_values(MATERIAL))

    def test_a_material_with_no_parameters_is_empty(self):
        self.assertEqual(sof_materials.material_values({"name": "x"}), {})
        self.assertEqual(sof_materials.material_values(None), {})


class CatalogTests(unittest.TestCase):
    def setUp(self):
        sof_materials.forget()

    def tearDown(self):
        sof_materials.forget()

    def test_no_client_is_an_empty_catalog_not_a_crash(self):
        self.assertEqual(sof_materials.catalog(None), ())

    def test_sorts_and_caches(self):
        class _Client:
            calls = 0

            def request_json(self, method, route):
                _Client.calls += 1
                return ["zulu", "alpha"]

        client = _Client()
        self.assertEqual(sof_materials.catalog(client), ("alpha", "zulu"))
        sof_materials.catalog(client)
        self.assertEqual(_Client.calls, 1)

    def test_a_failing_service_is_an_empty_catalog(self):
        class _Broken:
            def request_json(self, method, route):
                raise RuntimeError("no service")

        self.assertEqual(sof_materials.catalog(_Broken()), ())


if __name__ == "__main__":
    unittest.main()

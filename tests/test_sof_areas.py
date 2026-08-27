from pathlib import Path
import sys
import unittest


ADDONS = Path(__file__).resolve().parents[1] / "addons"
if str(ADDONS) not in sys.path:
    sys.path.insert(0, str(ADDONS))

from carbon_eve_resources import sof_areas  # noqa: E402


#: mde3_t3's opaque areas, verbatim from the live hull record. Two areas share
#: the name `area_sails` and both block slots 3 and 4.
MDE3_T3 = {
    "opaqueAreas": [
        {"name": "area_hull", "index": 0, "count": 1, "areaType": 0,
         "shader": "quad/quadv5.fx", "blockedMaterials": 0},
        {"name": "area_booster", "index": 1, "count": 1, "areaType": 0,
         "shader": "quad/quadheatv5.fx", "blockedMaterials": 0},
        {"name": "area_sails", "index": 2, "count": 1, "areaType": 2,
         "shader": "quad/quadsailsv5.fx", "blockedMaterials": 12},
        {"name": "area_sails", "index": 3, "count": 1, "areaType": 2,
         "shader": "quad/quadsailsv5.fx", "blockedMaterials": 12},
    ],
}


class HullAreaTests(unittest.TestCase):
    def test_flattens_every_bucket(self):
        areas = sof_areas.hull_areas(MDE3_T3)
        self.assertEqual(len(areas), 4)
        self.assertEqual([area["areaType"] for area in areas], [0, 0, 2, 2])

    def test_keeps_only_the_shader_file_name(self):
        areas = sof_areas.hull_areas(MDE3_T3)
        self.assertEqual(areas[0]["shader"], "quadv5.fx")

    def test_a_missing_record_is_no_areas_not_an_error(self):
        self.assertEqual(sof_areas.hull_areas(None), ())
        self.assertEqual(sof_areas.hull_areas({}), ())


class MatchTests(unittest.TestCase):
    def setUp(self):
        self.areas = sof_areas.hull_areas(MDE3_T3)

    def test_matches_on_index(self):
        area = sof_areas.match_area(self.areas, name="area_hull", index=0)
        self.assertEqual(area["areaType"], 0)
        self.assertEqual(area["blockedMaterials"], 0)

    def test_the_two_sails_are_told_apart_by_index(self):
        # A name match would give the second sails area the first one's record.
        # They agree here, but the mechanism must not depend on that.
        first = sof_areas.match_area(self.areas, name="area_sails", index=2)
        second = sof_areas.match_area(self.areas, name="area_sails", index=3)
        self.assertEqual(first["index"], 2)
        self.assertEqual(second["index"], 3)

    def test_the_sails_carry_their_mask(self):
        area = sof_areas.match_area(self.areas, name="area_sails", index=2)
        self.assertEqual(area["blockedMaterials"], 12)

    def test_a_name_that_disagrees_with_the_index_does_not_match(self):
        self.assertIsNone(
            sof_areas.match_area(self.areas, name="area_hull", index=2))

    def test_an_unambiguous_shader_can_stand_in_for_a_missing_index(self):
        area = sof_areas.match_area(self.areas, index=-1, shader="quadheatv5.fx")
        self.assertEqual(area["name"], "area_booster")

    def test_an_ambiguous_shader_is_refused_rather_than_guessed(self):
        # Two areas use quadsailsv5. Picking one would give an area a mask it
        # may not have, which is worse than admitting we cannot tell.
        self.assertIsNone(
            sof_areas.match_area(self.areas, index=-1, shader="quadsailsv5.fx"))

    def test_nothing_matches_an_empty_table(self):
        self.assertIsNone(sof_areas.match_area((), name="area_hull", index=0))


class _Material(dict):
    """Stands in for a Blender material, which is a dict for our purposes."""

    def __init__(self, name, **fields):
        super().__init__(**fields)
        self.name = name


class StampTests(unittest.TestCase):
    def test_records_the_type_and_the_mask(self):
        material = _Material("02 area_sails")
        area = sof_areas.hull_areas(MDE3_T3)[2]
        self.assertTrue(sof_areas.stamp_material(material, area))
        self.assertEqual(material["carbon_area_type"], 2)
        self.assertEqual(material["carbon_area_type_name"], "sails")
        self.assertEqual(material["carbon_blocked_materials"], 12)

    def test_an_unmatched_material_is_marked_unknown_not_primary(self):
        # Defaulting to primary would sweep it into every hull edit. -1 keeps
        # it out of all of them, which is the honest failure.
        material = _Material("mystery")
        self.assertFalse(sof_areas.stamp_material(material, None))
        self.assertEqual(material["carbon_area_type"], -1)
        self.assertEqual(material["carbon_area_type_name"], "unknown")


if __name__ == "__main__":
    unittest.main()

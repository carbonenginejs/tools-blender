"""Every locator on a hull, in one shape.

A locator is a place on a hull -- where a turret bolts on, where an engine
fires, where the camera looks. The SOF reports them in two different formats
and the same booster appears in both, which is the whole reason this module
exists: without it every consumer reads the document its own way and each one
gets the duplicates wrong differently.
"""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))

from carbon_eve_resources.core import locators  # noqa: E402


def document(named=(), sets=()):
    return {
        "locators": [{"name": name, "transform": list(range(16))}
                     for name in named],
        "locatorSets": [
            {"name": name, "locators": [
                {"position": [1.0, 2.0, 3.0], "direction": [0, 0, 0, 1],
                 "scale": [1, 1, 1], "boneIndex": bone}
                for bone in bones]}
            for name, bones in sets],
    }


class NameTests(unittest.TestCase):
    def test_the_name_is_the_type(self):
        """`locator_turret_1a` says turret. Carbon finds hardpoints this way."""

        found = locators.locators(document(named=(
            "locator_turret_1a", "locator_booster_3", "locator_audio_booster")))
        self.assertEqual([row.kind for row in found],
                         ["turret", "booster", "audio"])

    def test_an_unnamed_shape_still_arrives(self):
        found = locators.locators(document(named=("something_else",)))
        self.assertEqual(found[0].kind, "locator")
        self.assertEqual(found[0].name, "something_else")

    def test_each_kind_is_numbered_from_zero(self):
        found = locators.locators(document(named=(
            "locator_turret_1a", "locator_booster_1",
            "locator_turret_2a", "locator_booster_2")))
        self.assertEqual([(row.kind, row.index) for row in found],
                         [("turret", 0), ("booster", 0),
                          ("turret", 1), ("booster", 1)])


class SetTests(unittest.TestCase):
    def test_a_set_becomes_one_locator_per_entry(self):
        found = locators.locators(document(sets=(("damage", (-1, 0, 12)),)))
        self.assertEqual(len(found), 3)
        self.assertEqual([row.bone_index for row in found], [-1, 0, 12])
        self.assertEqual([row.set_name for row in found], ["damage"] * 3)

    def test_the_set_name_is_the_kind(self):
        found = locators.locators(document(sets=(("dockinglights_01", (-1,)),)))
        self.assertEqual(found[0].kind, "dockinglights_01")

    def test_direction_is_a_quaternion_despite_the_name(self):
        """It is a quaternion called a direction in the SOF itself.

        Reading it as a heading would silently unrotate every locator that has
        one, which is exactly the trap this records.
        """

        doc = document(sets=(("cargobay", (-1,)),))
        doc["locatorSets"][0]["locators"][0]["direction"] = [0.0, 0.7071, 0.0,
                                                            0.7071]
        found = locators.locators(doc)
        self.assertEqual(len(found[0].rotation), 4)
        self.assertAlmostEqual(found[0].rotation[3], 0.7071)


class DuplicateTests(unittest.TestCase):
    def test_a_booster_named_and_in_a_set_is_reported_once(self):
        """The same twelve places, said twice.

        `locator_booster_3` and the `boosters` set are the same engine. Taking
        both gives every hull twice the boosters it has, which is the kind of
        thing that looks fine until somebody counts.
        """

        found = locators.locators(document(
            named=("locator_booster_1", "locator_booster_2"),
            sets=(("boosters", (-1, -1)),)))
        self.assertEqual(len(found), 2)
        self.assertEqual([row.name for row in found],
                         ["locator_booster_1", "locator_booster_2"])

    def test_the_named_form_wins_because_it_carries_the_name(self):
        found = locators.locators(document(
            named=("locator_booster_7",), sets=(("boosters", (-1,)),)))
        self.assertEqual(found[0].name, "locator_booster_7")
        self.assertEqual(found[0].set_name, "")

    def test_a_set_with_no_named_equivalent_is_kept(self):
        found = locators.locators(document(
            named=("locator_turret_1a",), sets=(("damage", (-1, -1)),)))
        self.assertEqual(locators.kinds(found), {"turret": 1, "damage": 2})


class ShapeTests(unittest.TestCase):
    def test_a_named_locator_carries_its_matrix(self):
        found = locators.locators(document(named=("locator_turret_1a",)))
        self.assertEqual(len(found[0].transform), 16)

    def test_a_set_entry_carries_a_triple_and_no_matrix(self):
        found = locators.locators(document(sets=(("steam", (-1,)),)))
        self.assertIsNone(found[0].transform)
        self.assertEqual(found[0].position, (1.0, 2.0, 3.0))

    def test_of_kind_selects_in_document_order(self):
        found = locators.locators(document(named=(
            "locator_turret_1a", "locator_booster_1", "locator_turret_2a")))
        self.assertEqual([row.name for row in locators.of_kind(found, "turret")],
                         ["locator_turret_1a", "locator_turret_2a"])

    def test_a_hull_with_nothing_gives_nothing(self):
        self.assertEqual(locators.locators({}), [])


if __name__ == "__main__":
    unittest.main()

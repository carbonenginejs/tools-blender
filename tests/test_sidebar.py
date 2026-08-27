"""The panels must act on the ship the person is touching, and no other."""

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
    from carbon_eve_resources import sidebar, sof_panels


class _Context:
    """Just the one attribute the lookup reads."""

    def __init__(self, obj):
        self.object = obj


@unittest.skipIf(bpy is None, "needs Blender")
class ShipLookupTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        try:
            sof_panels.register()
        except Exception:
            pass
        self.a, self.a_decal = self._ship("ship_a", "mde3_t3:legion_minmatar:minmatar")

    def _ship(self, name, dna):
        hull = bpy.data.objects.new(name, None)
        bpy.context.scene.collection.objects.link(hull)
        hull.carbon_sof.dna = dna
        child = bpy.data.objects.new(f"{name}_decal", None)
        bpy.context.scene.collection.objects.link(child)
        child.parent = hull
        return hull, child

    def test_walks_up_from_a_decal_to_its_ship(self):
        # A person clicks a decal far more often than the hull.
        self.assertIs(sidebar._ship_of(_Context(self.a_decal)), self.a)

    def test_one_ship_needs_no_selection(self):
        self.assertIs(sidebar._ship_of(_Context(None)), self.a)

    def test_two_ships_and_no_selection_is_refused_not_guessed(self):
        # The defect: with two ships open, every edit landed on whichever came
        # first in the file, however carefully the other was selected.
        self._ship("ship_b", "ab1_t1:amarrbase:amarr")
        self.assertIsNone(sidebar._ship_of(_Context(None)))

    def test_each_ship_is_found_from_its_own_parts(self):
        b, b_decal = self._ship("ship_b", "ab1_t1:amarrbase:amarr")
        self.assertIs(sidebar._ship_of(_Context(self.a_decal)), self.a)
        self.assertIs(sidebar._ship_of(_Context(b_decal)), b)

    def test_an_object_belonging_to_no_ship_finds_none(self):
        self._ship("ship_b", "ab1_t1:amarrbase:amarr")
        stray = bpy.data.objects.new("stray", None)
        bpy.context.scene.collection.objects.link(stray)
        self.assertIsNone(sidebar._ship_of(_Context(stray)))


if __name__ == "__main__":
    unittest.main()

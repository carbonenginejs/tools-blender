"""Which object the ship hangs off.

A person grabs a ship by clicking its hull. If the hull is a leaf, that moves
the hull and leaves the skeleton, the banners and every plane behind -- which
is what happened, and it looks like a broken import rather than a parenting
choice.
"""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))

from carbon_eve_resources.ship import ship_anchor  # noqa: E402


class Data:
    def __init__(self, vertices):
        self.vertices = [None] * vertices


class Modifier:
    def __init__(self, type_, object_=None):
        self.type = type_
        self.object = object_


class Fake:
    """Enough of an object for the choice: type, modifiers, vertex count."""

    def __init__(self, name, type_="MESH", vertices=0, modifiers=()):
        self.name = name
        self.type = type_
        self.data = Data(vertices) if type_ == "MESH" else None
        self.modifiers = list(modifiers)


class AnchorTests(unittest.TestCase):
    def test_the_biggest_mesh_is_the_hull(self):
        # Biggest rather than first: import order belongs to the GR2 loader,
        # and a banner plane would otherwise be able to win.
        banner = Fake("banner_corp_logo", vertices=4)
        hull = Fake("ab1_TShape1", vertices=12000)
        self.assertIs(ship_anchor([banner, hull, Fake("plane_0_0", vertices=4)]),
                      hull)

    def test_an_armature_that_deforms_something_wins(self):
        # Blender deforms a mesh by an armature it is a CHILD of. Inverting
        # that to make the gesture nicer would break the rig.
        rig = Fake("ab1_skeleton", type_="ARMATURE")
        hull = Fake("ab1_TShape1", vertices=12000,
                    modifiers=[Modifier("ARMATURE", rig)])
        self.assertIs(ship_anchor([hull, rig]), rig)

    def test_an_armature_that_deforms_nothing_does_not_win(self):
        # Every ship carries a skeleton; almost none of them are skinned. An
        # armature with no modifier pointing at it is bones, not a rig.
        rig = Fake("ab1_skeleton", type_="ARMATURE")
        hull = Fake("ab1_TShape1", vertices=12000)
        self.assertIs(ship_anchor([rig, hull]), hull)

    def test_an_unbound_armature_modifier_is_not_an_anchor(self):
        # A modifier whose object is None would otherwise be returned as the
        # parent of the whole ship.
        hull = Fake("ab1_TShape1", vertices=12000,
                    modifiers=[Modifier("ARMATURE", None)])
        self.assertIs(ship_anchor([hull]), hull)

    def test_nothing_to_anchor_to_is_not_an_error(self):
        # A document that assembled no geometry still has to finish building.
        self.assertIsNone(ship_anchor([Fake("empty", type_="EMPTY")]))
        self.assertIsNone(ship_anchor([]))


if __name__ == "__main__":
    unittest.main()

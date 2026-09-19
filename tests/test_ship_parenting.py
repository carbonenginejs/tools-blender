"""Which object the ship hangs off.

A person grabs a ship by clicking its hull. If the hull is a leaf, that moves
the hull and leaves the skeleton, the banners and every plane behind -- which
is what happened, and it looks like a broken import rather than a parenting
choice.
"""

from pathlib import Path
import sys
import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

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


class MeshOwnershipTests(unittest.TestCase):
    def test_overlay_does_not_become_primary_and_child_only_hull_is_valid(self):
        from carbon_eve_resources import ship
        overlay = {"_type": "Tr2Mesh", "geometryResPath": "res:/shield.cmf"}
        child = {"_type": "Tr2Mesh", "geometryResPath": "res:/child.cmf"}
        document = {"impactOverlay": {"_type": "EveImpactOverlay", "mesh": overlay},
                    "mesh": {"_type": "Tr2Mesh", "geometryResPath": ""},
                    "children": [{"mesh": child}]}
        self.assertNotIn(overlay, ship.find_meshes(document, include_overlays=False))
        self.assertIn(child, ship.find_meshes(document, include_overlays=False))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mesh"
            path.write_bytes(b"fixture")
            resources = {"res:/shield.cmf": str(path), "res:/child.cmf": str(path)}
            imported = []
            def import_mesh(local, label, logical):
                obj = SimpleNamespace(name=logical, data=SimpleNamespace(materials=[]))
                imported.append(obj)
                return [obj]
            with patch.object(ship, "import_geometry", side_effect=import_mesh):
                result = ship.assemble("", "", document=document, resources=resources, family=object(), clear=False)
            self.assertEqual(result.name, "res:/child.cmf")
            self.assertEqual([obj.name for obj in imported], ["res:/child.cmf", "res:/shield.cmf"])
            document["children"] = []
            with patch.object(ship, "import_geometry") as importer:
                # clear=True must not destroy the existing scene for this failure.
                with patch.object(ship, "bpy", None):
                    self.assertIsNone(ship.assemble("", "", document=document, resources=resources, family=object(), clear=True))
                importer.assert_not_called()


class AnchorTests(unittest.TestCase):
    def test_the_biggest_mesh_is_the_hull(self):
        # Biggest rather than first: import order belongs to the GR2 loader,
        # and a banner plane would otherwise be able to win.
        banner = Fake("banner_corp_logo", vertices=4)
        hull = Fake("ab1_TShape1", vertices=12000)
        self.assertIs(ship_anchor([banner, hull, Fake("plane_0_0", vertices=4)]),
                      hull)

    def test_the_hull_wins_even_when_an_armature_deforms_it(self):
        # Inverting Blender's usual mesh-under-armature. Measured on a Legion
        # rather than assumed: reparenting moved the evaluated geometry by
        # 0.0000, and moving the hull by 100 moved the banners, the rig and
        # the DEFORMED geometry by exactly 100 each. The deform is relative,
        # so it does not care which of the two is the parent -- only that they
        # move together, which parenting is what guarantees.
        rig = Fake("legion_skeleton", type_="ARMATURE")
        hull = Fake("legion_TShape1", vertices=12000,
                    modifiers=[Modifier("ARMATURE", rig)])
        self.assertIs(ship_anchor([hull, rig]), hull)

    def test_an_armature_is_never_the_anchor(self):
        # Every ship carries a skeleton, and it is never the thing a person
        # clicks.
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

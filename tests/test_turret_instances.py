"""Fitted hardpoints keep independent rigs and per-hull material bindings."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))
try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipUnless(bpy, "requires Blender")
class TurretInstanceTests(unittest.TestCase):
    def test_shared_geometry_keeps_per_hull_materials_and_per_mount_rigs(self):
        from carbon_eve_resources import turrets
        before = set(bpy.data.objects)
        materials = []
        locators = []
        try:
            for index in range(2):
                hull = bpy.data.objects.new(f"test_hull_{index}", None)
                hull["carbon_sof_dna"] = "hull:faction:race"
                bpy.context.scene.collection.objects.link(hull)
                for bay in range(2):
                    locator = bpy.data.objects.new(f"test_locator_{index}_{bay}", None)
                    bpy.context.scene.collection.objects.link(locator)
                    locator.parent = hull
                    locators.append(locator)

            def import_geometry(*args, **kwargs):
                rig = bpy.data.objects.new("test_rig", bpy.data.armatures.new("test_rig"))
                mesh = bpy.data.objects.new("test_mesh", bpy.data.meshes.new("test_mesh"))
                for obj in (rig, mesh):
                    bpy.context.scene.collection.objects.link(obj)
                mesh.parent = rig
                mesh.modifiers.new("Armature", "ARMATURE").object = rig

            def make_material(*args):
                material = bpy.data.materials.new("test_turret_material")
                materials.append(material)
                return material

            with patch.object(turrets.ship_module, "import_geometry", import_geometry), \
                 patch.object(turrets, "_turret_material", make_material), \
                 patch.object(turrets, "ship_of", lambda locator: locator.parent):
                count, _ = turrets.fit(bpy.context, {"geometryResPath": "res:/test.cmf"},
                                      {"res:/test.cmf": "test.cmf"}, "res:/test.black",
                                      "test", locators=locators)
            self.assertEqual(count, 4)
            self.assertEqual(len(materials), 2)
            meshes = [obj for obj in bpy.data.objects if obj not in before and obj.type == "MESH"]
            self.assertEqual(len(meshes), 4)
            self.assertEqual(len({obj.data for obj in meshes}), 1)
            for mesh in meshes:
                self.assertIs(mesh.modifiers[0].object, mesh.parent)
                locator = mesh.parent.parent.parent
                expected = materials[0 if locator in locators[:2] else 1]
                self.assertEqual(mesh.material_slots[0].link, "OBJECT")
                self.assertIs(mesh.material_slots[0].material, expected)
        finally:
            for obj in set(bpy.data.objects) - before:
                bpy.data.objects.remove(obj, do_unlink=True)
            for material in materials:
                bpy.data.materials.remove(material)

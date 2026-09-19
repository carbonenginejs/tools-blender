"""Native haze must preserve the source and follow its evaluated geometry."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))
import bpy
from carbon_eve_resources.quad import native_heat
from carbon_eve_resources.quad.interface import load_family


class NativeHeatTests(unittest.TestCase):
    def setUp(self):
        self.objects = set(bpy.data.objects)
        self.member = next(m for m in load_family(target="frontier").members.values()
                           if m.name == "fxheatdistortionv5")
        mesh = bpy.data.meshes.new("native haze source")
        mesh.from_pydata([(0,0,0),(1,0,0),(1,1,0),(0,1,0),
                         (2,0,0),(3,0,0),(3,1,0),(2,1,0)], [],
                        [(0,1,2,3),(4,5,6,7)])
        self.source = bpy.data.objects.new("native haze source", mesh)
        bpy.context.collection.objects.link(self.source)
        for name in ("base zero", "base one"):
            mesh.materials.append(bpy.data.materials.new(name))
        mesh.polygons[1].material_index = 1
        self.source.shape_key_add(name="Basis")
        key = self.source.shape_key_add(name="Move")
        key.value = 0
        for v in key.data:
            v.co.z += 2
        self.area = {"index": 0, "count": 1, "effect": {
            "effectFilePath": self.member.effect_path}}

    def tearDown(self):
        for obj in set(bpy.data.objects) - self.objects:
            bpy.data.objects.remove(obj, do_unlink=True)

    def evaluate(self, obj):
        self.source.update_tag()
        bpy.context.view_layer.update()
        return obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data

    def test_disabled_by_default_and_tracks_only_authored_area_with_morph(self):
        original = self.source.data.as_pointer()
        materials = tuple(self.source.data.materials)
        shell = native_heat.attach(self.source, self.source, self.area, self.member, {}, 10)
        self.assertEqual(len(self.evaluate(shell).polygons), 0)
        self.source[native_heat.STRENGTH] = 1.0
        mesh = self.evaluate(shell)
        self.assertEqual(len(mesh.polygons), 1)
        self.assertEqual(len(mesh.vertices), 4)
        self.assertTrue(all(abs(v.co.z - .1) < 1e-5 for v in mesh.vertices))
        self.source.data.shape_keys.key_blocks['Move'].value = .5
        mesh = self.evaluate(shell)
        self.assertTrue(all(abs(v.co.z - 1.1) < 1e-5 for v in mesh.vertices))
        self.source.location = (20, 30, 40)
        mesh = self.evaluate(shell)
        self.assertTrue(all(abs(v.co.z - 1.1) < 1e-5 for v in mesh.vertices))
        self.assertEqual(self.source.data.as_pointer(), original)
        self.assertEqual(tuple(self.source.data.materials), materials)
        self.assertEqual(len(shell.material_slots), 0)
        self.assertIs(shell.parent, self.source)
        self.source[native_heat.STRENGTH] = 0.0
        self.assertEqual(len(self.evaluate(shell).polygons), 0)

    def test_invalid_area_and_bound_volume_do_not_create_helpers(self):
        before = set(bpy.data.objects)
        for index in (-1, 2):
            with self.assertRaises(ValueError):
                native_heat.attach(self.source, self.source, dict(self.area, index=index), self.member, {}, 10)
        self.area['effect']['resources'] = [{'name':'NoiseMap','resourcePath':'res:/noise.dds'}]
        with self.assertRaisesRegex(ValueError, '3D volume'):
            native_heat.attach(self.source, self.source, self.area, self.member, {}, 10)
        self.assertEqual(before, set(bpy.data.objects))
        self.assertNotIn(native_heat.STRENGTH, self.source)


if __name__ == '__main__':
    unittest.main()

"""Evaluated rigid palette preserves positions and unnormalized authored frames."""
import sys
from pathlib import Path
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
try:
    import bpy
    from mathutils import Matrix, Vector
except ImportError:
    bpy = None


@unittest.skipUnless(bpy, "needs Blender")
class RigidFrameTests(unittest.TestCase):
    def test_palette_binding_inverse_bind_and_object_transforms(self):
        from carbon_eve_resources.gr2_importer import addon
        from carbon_eve_resources.gr2_importer.skinning import attach_rigid_frame
        inverse = [Matrix.Translation((-9, 0, 0)), Matrix.Translation((-10, -1, 0))]
        skeleton = {"bones": ["root", "child"], "parents": [-1, 0],
            "restTransforms": [{"position": [10,0,0]}, {"position": [0,2,0]}],
            "invBindTransforms": [[v for row in matrix.transposed() for v in row] for matrix in inverse]}
        arm, _ = addon.import_armature({"skeletons": [skeleton]}, bpy.context.scene.collection, "frame test", 1, 0, "LOCAL_Y")
        data = bpy.data.meshes.new("frame test")
        positions = [(0,0,0), (1,0,0), (0,1,0)]
        data.from_pydata(positions, [], [(0,1,2)])
        obj = bpy.data.objects.new("frame test", data)
        bpy.context.scene.collection.objects.link(obj)
        frame = {"gr2_tangent": (2,0,0), "gr2_binormal": (0,3,0), "gr2_normal": (0,0,4)}
        for name, vector in frame.items():
            attr = data.attributes.new(name=name, type="FLOAT_VECTOR", domain="POINT")
            attr.data.foreach_set("vector", list(vector) * 3)
        entry = {"vertex": {"position": [v for p in positions for v in p],
                    "blendIndice": [0,0,0,0, 1,0,0,0, 0,0,0,0]},
                 "boneBindings": [{"name": "child"}, {"name": "root"}]}
        addon.apply_skinning(obj, entry, arm)
        arm.matrix_world = Matrix.Translation((3,-2,1)) @ Matrix.Rotation(.2, 4, 'Z')
        obj.matrix_world = Matrix.Translation((1,3,2)) @ Matrix.Rotation(-.3, 4, 'Z')
        arm.pose.bones["root"].rotation_mode = "XYZ"
        arm.pose.bones["root"].rotation_euler.z = .7
        arm.pose.bones["root"].scale = (2,.5,1.5)
        bpy.context.view_layer.update()
        helper = None
        tree = None
        try:
            modifier = attach_rigid_frame(obj)
            tree = modifier.node_group
            helper = arm["carbon_rigid_helper"]
            self.assertEqual(attach_rigid_frame(obj).as_pointer(), modifier.as_pointer())
            self.assertEqual(len(helper.data.vertices), 8)
            self.assertFalse(any(m.type == "ARMATURE" for m in obj.modifiers))
            bpy.context.view_layer.update()
            evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
            for vertex, bone_index in enumerate((1,0,1)):
                name = ("root", "child")[bone_index]
                matrix = obj.matrix_world.inverted() @ arm.matrix_world @ arm.pose.bones[name].matrix @ inverse[bone_index]
                expected = matrix @ Vector(positions[vertex])
                self.assertLess((evaluated.data.vertices[vertex].co - expected).length, 1e-4)
                for attribute, vector in frame.items():
                    actual = evaluated.data.attributes[attribute].data[vertex].vector
                    self.assertLess((actual - matrix.to_3x3() @ Vector(vector)).length, 1e-4)
            obj.shape_key_add(name="Basis")
            morph = obj.shape_key_add(name="offset")
            for vertex in morph.data:
                vertex.co.x += 1
            morph.value = .25
            bpy.context.view_layer.update()
            evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
            matrix = obj.matrix_world.inverted() @ arm.matrix_world @ arm.pose.bones["child"].matrix @ inverse[1]
            self.assertLess((evaluated.data.vertices[0].co - matrix @ Vector((.25,0,0))).length, 1e-4)
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(data)
            if helper:
                helper_data = helper.data
                bpy.data.objects.remove(helper, do_unlink=True)
                bpy.data.meshes.remove(helper_data)
            if tree:
                bpy.data.node_groups.remove(tree)
            arm_data = arm.data
            bpy.data.objects.remove(arm, do_unlink=True)
            bpy.data.armatures.remove(arm_data)


if __name__ == "__main__":
    unittest.main()

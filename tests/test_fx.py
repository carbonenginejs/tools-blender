"""EVE's fxv5 graph and the fx vertex view, for both targets' members."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))
from carbon_eve_resources.quad.interface import load_family
from test_frontier_nodes import evaluate
try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipIf(bpy is None, "needs Blender")
class FxGraphTests(unittest.TestCase):
    def setUp(self):
        from carbon_eve_resources.quad import fx
        self.fx = fx
        self.members = (load_family().member("fxv5.fx"),
                        next(m for m in load_family(target="frontier").members.values() if m.name == "fxv5"))

    def test_both_targets_share_one_graph_but_not_one_group(self):
        eve, frontier = self.members
        self.assertEqual((eve.target, frontier.target), ("eve", "frontier"))
        self.assertIsNot(self.fx.build_group(eve), self.fx.build_group(frontier))

    def test_areas_of_both_targets_build_through_fx(self):
        from carbon_eve_resources.quad.materials import build_area_material
        # Frontier measured no skinned fxv5 alias; EVE's skinned_ spelling
        # compiles to the bare pixel program.
        paths = {"eve": "res:/graphics/effect/managed/space/spaceobject/v5/fx/skinned_fxv5.fx",
                 "frontier": self.members[1].effect_path}
        for member in self.members:
            with self.subTest(target=member.target):
                material, problem = build_area_material({"effect": {
                    "effectFilePath": paths[member.target], "options": dict(member.selected_options)}},
                    load_family(target=member.target), {}, 0)
                self.assertIsNone(problem)
                self.addCleanup(bpy.data.materials.remove, material)
                group = next(n for n in material.node_tree.nodes if n.bl_idname == "ShaderNodeGroup")
                self.assertIs(group.node_tree, self.fx.build_group(member))
                self.assertTrue(material["carbon_fx_vertex_view"])
                self.assertEqual("carbon_normal_limit" in material, member.target == "eve")

    def test_fresnel_preserves_interpolated_view_and_independent_alpha(self):
        # EVE's member and Frontier's inherited copy build the same arithmetic.
        for member in self.members:
            with self.subTest(target=member.target):
                self.fresnel(member)

    def fresnel(self, member):
        tree = self.fx.build_group(member)
        values = {"Layer1Map": (.5, .4, .3), "Layer2Map": (.2, .3, .4),
                  "LayerMaskMap": (1, 1, 1), "BaseColor": (2, 3, 4),
                  "Layer1MapAlpha": .1, "Layer2MapAlpha": .2, "LayerMaskMapAlpha": .3,
                  "BaseColorAlpha": .4, "activationStrength": .5,
                  "FresnelFactors.x": 1, "FresnelFactors.y": .5, "FresnelFactors.z": 0}
        attributes = {"carbon_fx_vertex_view": (0, 0, .25), "gr2_normal": (0, 0, 1)}
        color = evaluate(tree, "Emission", values, attributes)
        for actual, expected in zip(color, (.0375, .0675, .09)):
            self.assertAlmostEqual(actual, expected, places=6)
        self.assertAlmostEqual(evaluate(tree, "Alpha", values, attributes), .0012, places=6)
        values["BaseColorAlpha"] = 0
        self.assertEqual(evaluate(tree, "Emission", values, attributes), color)
        values["FresnelFactors.y"] = -2
        for actual, expected in zip(evaluate(tree, "Emission", values, attributes), (.05, .09, .12)):
            self.assertAlmostEqual(actual, expected, places=6)

    def test_vertex_view_tracks_camera_replacement_and_parent_shear(self):
        from mathutils import Matrix
        mesh = bpy.data.meshes.new("fx view test")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        obj = bpy.data.objects.new("fx view test", mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.matrix_world = Matrix(((2, .4, 0, 3), (0, 1, 0, -2), (0, 0, .5, 1), (0, 0, 0, 1)))
        old_camera = bpy.context.scene.camera
        cameras = []
        try:
            modifier = self.fx.attach_vertex_view(obj, bpy.context.scene)
            self.assertEqual(self.fx.attach_vertex_view(obj, bpy.context.scene), modifier)
            # Existing files upgrade in place without adding another modifier.
            modifier.node_group["carbon_fx_vertex_version"] = 1
            self.assertEqual(self.fx.attach_vertex_view(obj, bpy.context.scene), modifier)
            self.assertEqual(len(obj.modifiers), 1)
            self.assertEqual(modifier.node_group["carbon_fx_vertex_version"], 2)
            for location in ((10, 2, 4), (4, 5, 8)):
                camera = bpy.data.objects.new("fx camera", bpy.data.cameras.new("fx camera"))
                cameras.append(camera)
                bpy.context.scene.collection.objects.link(camera)
                camera.location = location
                bpy.context.scene.camera = camera
                bpy.context.view_layer.update()
                depsgraph = bpy.context.evaluated_depsgraph_get()
                evaluated = obj.evaluated_get(depsgraph)
                attribute = evaluated.data.attributes["carbon_fx_vertex_view"]
                distances = evaluated.data.attributes["carbon_fx_vertex_distance"]
                for vertex, stored, distance in zip(mesh.vertices, attribute.data, distances.data):
                    delta = camera.matrix_world.translation - obj.matrix_world @ vertex.co
                    expected = delta.normalized()
                    self.assertLess((expected - stored.vector).length, 1e-6)
                    self.assertAlmostEqual(delta.length, distance.value, places=5)
        finally:
            bpy.context.scene.camera = old_camera
            tree = modifier.node_group
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.node_groups.remove(tree)
            bpy.data.meshes.remove(mesh)
            for camera in cameras:
                data = camera.data
                bpy.data.objects.remove(camera, do_unlink=True)
                bpy.data.cameras.remove(data)


if __name__ == "__main__":
    unittest.main()

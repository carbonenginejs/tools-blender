"""EveSpotlightSet's cone and glow, against TQ 3542233 spotlight*pool.sm_depth."""
import math
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


class SpotlightInterfaceTests(unittest.TestCase):
    def test_measured_members(self):
        family = load_family()
        cone = family.member("res:/graphics/effect/managed/space/spaceobject/fx/spotlightconepool.fx")
        glow = family.member("res:/graphics/effect/managed/space/spaceobject/fx/spotlightglowpool.fx")
        self.assertEqual((cone.textures, glow.textures), (("TextureMap",), ("TextureMap",)))
        self.assertEqual(glow.scene_textures, ("EveSceneFogVolumeMap", "DepthMap"))
        for member in (cone, glow):
            self.assertFalse(member.annotation("TextureMap").srgb)


@unittest.skipIf(bpy is None, "needs Blender")
class SpotlightGraphTests(unittest.TestCase):
    def setUp(self):
        from carbon_eve_resources.quad import spotlight
        self.spotlight = spotlight
        self.family = load_family()

    def test_cone_is_four_fins_through_the_axis(self):
        mesh = self.spotlight.cone_mesh("cone test", -.02)
        self.addCleanup(bpy.data.meshes.remove, mesh)
        self.assertEqual((len(mesh.vertices), len(mesh.polygons)), (16, 4))
        normals = mesh.attributes[self.spotlight.FIN_NORMAL].data
        for quad in range(4):
            angle = quad * math.pi / 4
            for corner, (x, y) in enumerate(self.spotlight.SELECTORS):
                vertex = mesh.vertices[quad * 4 + corner].co
                expected = (math.cos(angle + math.pi * x), math.sin(angle + math.pi * x), -.02 + y)
                for a, b in zip(vertex, expected):
                    self.assertAlmostEqual(a, b, places=6)
                # The fin normal is perpendicular to the fin's horizontal edge.
                normal = normals[quad * 4 + corner].vector
                self.assertAlmostEqual(normal.x * math.cos(angle) + normal.y * math.sin(angle), 0, places=6)
                self.assertEqual(normal.z, 0)

    def test_cone_colour_scales_by_activation_boost_and_facing(self):
        tree = self.spotlight.build_cone_group(self.family.member("spotlightconepool.fx"))
        values = {"TextureMap": (.5, .5, 1), "Color": (1, 2, 4, 1), "activationStrength": .5,
                  "boosterGain": 3.0, "boosterGainInfluence": 1.0}
        edge = (math.cos(math.radians(60)), 0, math.sin(math.radians(60)))
        attributes = {"carbon_fx_vertex_view": edge, self.spotlight.FIN_NORMAL: (1, 0, 0)}
        # .5 activation * (1 + (3 - 1) * 1) boost * .5 facing = .75
        for a, b in zip(evaluate(tree, "Emission", values, attributes), (.375, .75, 3.0)):
            self.assertAlmostEqual(a, b, places=5)
        values["boosterGainInfluence"] = 0.0
        self.assertAlmostEqual(evaluate(tree, "Emission", values, attributes)[0], .125, places=5)

    def test_glow_selects_sprite_or_flare_colour(self):
        tree = self.spotlight.build_glow_group(self.family.member("spotlightglowpool.fx"))
        values = {"TextureMap": (1, 1, 1), "SpriteColor": (1, 0, 0, 1), "FlareColor": (0, 1, 0, 1),
                  "activationStrength": 1.0, "boosterGain": 1.0, "boosterGainInfluence": 0.0}
        for quad, expected in ((0, (1, 0, 0)), (1, (0, 1, 0))):
            actual = evaluate(tree, "Emission", values, {self.spotlight.GLOW_QUAD: quad})
            for a, b in zip(actual, expected):
                self.assertAlmostEqual(a, b, places=6)

    def test_billboard_faces_the_camera_and_shrinks_off_axis(self):
        from mathutils import Matrix, Vector
        mesh = self.spotlight.glow_mesh("glow test")
        obj = bpy.data.objects.new("glow test", mesh)
        bpy.context.scene.collection.objects.link(obj)
        # Non-uniform scale and a rotation: the inverse must undo both.
        obj.matrix_world = Matrix.Translation((1, 2, 3)) @ Matrix.Rotation(.4, 4, "X") @ Matrix.Diagonal((2, .5, 3, 1))
        camera = bpy.data.objects.new("glow camera", bpy.data.cameras.new("glow camera"))
        bpy.context.scene.collection.objects.link(camera)
        camera.matrix_world = Matrix.Translation((4, -6, 9)) @ Matrix.Rotation(.7, 4, "Z") @ Matrix.Rotation(1.1, 4, "X")
        old_camera = bpy.context.scene.camera
        bpy.context.scene.camera = camera
        try:
            unit, scale = .01, (20, 40, 12)
            self.spotlight.attach_billboard(obj, bpy.context.scene, scale, unit)
            bpy.context.view_layer.update()
            evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
            world = obj.matrix_world
            centre = world.translation
            view = (camera.matrix_world.translation - centre).normalized()
            axis = world.to_3x3().col[2].normalized()
            right, up = (camera.matrix_world.to_3x3().col[i].normalized() for i in (0, 1))
            for index, vertex in enumerate(evaluated.data.vertices):
                quad, corner = divmod(index, 4)
                x, y = self.spotlight.SELECTORS[corner]
                facing = min(max((1 - quad) * self.spotlight.SPRITE_MIN_FACING, view.dot(axis)), 1)
                width = (scale[0] if quad == 0 else scale[1]) * unit * facing
                height = (scale[0] if quad == 0 else scale[2]) * unit * facing
                expected = (centre + right * (x - .5) * width + up * (y - .5) * height
                            + view * max(width, height) * .5)
                self.assertLess((world @ vertex.co - expected).length, 1e-5, index)
        finally:
            bpy.context.scene.camera = old_camera
            tree = obj.modifiers[0].node_group
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.node_groups.remove(tree)
            bpy.data.meshes.remove(mesh)
            data = camera.data
            bpy.data.objects.remove(camera, do_unlink=True)
            bpy.data.cameras.remove(data)


if __name__ == "__main__":
    unittest.main()

"""EvePlaneSet's planeglow, against TQ 3542233 planeglow.sm_depth."""
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

TIME = "Time (seconds)"


def frac(x):
    return x - math.floor(x)


def blink_reference(rate, phase, kind, time):
    """VS29-75, transcribed from the listing."""
    kind = math.trunc(abs(kind))
    if kind == 1:
        t = frac(time * rate + phase)
        rise = 1.0 if rate < 0.001999999862164259 else rate * 0.05000000074505806
        fall = rate * 0.20000000298023224
        if t < rise:
            return t / rise
        return 1 - (t - rise) / (fall - rise) if t < fall else 0.0
    if kind == 2:
        return frac((phase + time) * rate)
    if kind == 3:
        return 1 - frac((phase + time) * rate)
    if kind == 4:
        x = rate * time
        turns = frac(abs(x)) if x >= -x else -frac(abs(x))
        return (math.sin(turns * 6.2831854820251465 + phase * 6.2831854820251465) + 1) * 0.5
    return 1.0


class PlaneglowInterfaceTests(unittest.TestCase):
    def test_measured_member(self):
        member = load_family().member("res:/graphics/effect/managed/space/spaceobject/fx/skinned_planeglow.fx")
        self.assertEqual(member.name, "planeglow")
        self.assertEqual(member.textures, ("Layer1Map", "Layer2Map", "MaskMap"))
        self.assertEqual(member.constant("PlaneData").default, (1, 1, 1, 1))
        for texture in member.textures:
            self.assertFalse(member.annotation(texture).srgb, texture)


@unittest.skipIf(bpy is None, "needs Blender")
class PlaneglowGraphTests(unittest.TestCase):
    def setUp(self):
        from carbon_eve_resources.quad import planeglow
        self.planeglow = planeglow
        self.member = load_family().member("planeglow.fx")
        self.tree = planeglow.build_group(self.member)
        self.white = {"Layer1Map": (1, 1, 1), "Layer2Map": (1, 1, 1), "MaskMap": (1, 1, 1),
                      "Color": (1, 1, 1, 1), "activationStrength": 1.0, "PlaneData.x": 0.0}

    def emission(self, values, time=0.0, incoming=(0, 0, 1)):
        return evaluate(self.tree, "Emission", {**self.white, **values},
                        uv={TIME: time, "Incoming": incoming})

    def test_blink_curves_follow_the_vertex_stage(self):
        cases = [(0, .7, .1), (1, .3, .2), (1, .001, .5), (1, 5, .1), (2, .4, .3),
                 (3, .4, .3), (4, .25, .1), (4, -.6, .2), (5, .5, .5), (-2, .4, 0)]
        for kind, rate, phase in cases:
            for time in (0.0, .37, 1.9, 12.25):
                with self.subTest(kind=kind, rate=rate, phase=phase, time=time):
                    actual = self.emission({"blinkData.x": rate, "blinkData.y": phase, "blinkData.w": kind}, time)
                    self.assertAlmostEqual(actual[0], blink_reference(rate, phase, kind, time), places=5)

    def test_colour_activation_and_maps_multiply(self):
        actual = self.emission({"Layer1Map": (.5, 1, 1), "Layer2Map": (1, .5, 1), "MaskMap": (1, 1, .5),
                                "Color": (2, 3, 4, 0), "activationStrength": .5})
        for a, b in zip(actual, (.5, .75, 1.0)):
            self.assertAlmostEqual(a, b, places=6)

    def test_facing_scales_by_plane_data_x(self):
        edge = (math.sin(math.radians(60)), 0, math.cos(math.radians(60)))
        self.assertAlmostEqual(self.emission({"PlaneData.x": 1.0}, incoming=edge)[0], .5, places=5)
        self.assertAlmostEqual(self.emission({"PlaneData.x": 0.0}, incoming=edge)[0], 1.0, places=5)
        # |dot|: seen from behind is the same as from the front.
        self.assertAlmostEqual(self.emission({"PlaneData.x": 1.0}, incoming=(0, 0, -1))[0], 1.0, places=5)

    def material(self, item, plane_data=(1, 1, 1, 1)):
        material = self.planeglow.build_material("plane test", self.member, item, plane_data, {})
        self.addCleanup(bpy.data.materials.remove, material)
        return material

    def sample(self, material, texture, uv, time=0.0):
        node = next(n for n in material.node_tree.nodes if n.bl_idname == "ShaderNodeTexImage" and n.label == texture)
        return evaluate(material.node_tree, uv={"UV0": uv, TIME: time}, socket=node.inputs["Vector"])

    def test_mask_reads_its_atlas_cell(self):
        # A 4 x 2 grid (PlaneData.y * z, y * w); cell 5 is column 1, row 1.
        material = self.material({"maskAtlasID": 5}, (1, 1, 4, 2))
        u, v = .5, .25                                   # Blender UV; authored v = .75
        expected = (u / 4 + .25, 1 - (1 + (1 - v)) / 2)  # back to image space
        for a, b in zip(self.sample(material, "MaskMap", (u, v, 0)), expected):
            self.assertAlmostEqual(a, b, places=6)

    def test_layers_scroll_with_half_float_data(self):
        item = {"layer1Transform": (2, 3, .1, .2), "layer1Scroll": (.3, .7, .05, 0)}
        material = self.material(item)
        half = self.planeglow.half
        u, v, time = .25, .5, 3.0
        authored = (u * 2 + half(.1) + half(.3) * time + half(.05), (1 - v) * 3 + half(.2) + half(.7) * time)
        for a, b in zip(self.sample(material, "Layer1Map", (u, v, 0), time), (authored[0], 1 - authored[1])):
            self.assertAlmostEqual(a, b, places=5)

    def test_additive_and_double_sided(self):
        material = self.material({})
        self.assertFalse(material.use_backface_culling)
        if hasattr(material, "surface_render_method"):
            self.assertEqual(material.surface_render_method, "BLENDED")


if __name__ == "__main__":
    unittest.main()

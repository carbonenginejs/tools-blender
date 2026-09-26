"""Attachment attenuation against the measured EVE radial expression."""
import math
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))
try:
    import bpy
except ImportError:
    bpy = None

@unittest.skipIf(bpy is None, "needs Blender")
class AttachmentLightTests(unittest.TestCase):
    def make_light(self, inner=8):
        from carbon_eve_resources import ship
        obj = ship.attachment_light({'lightData': {'radius': 10,
            'innerRadius': inner, 'brightness': 2, 'color': [1, 2, 1, 1]}},
            'test attachment', None, None)
        self.addCleanup(bpy.data.lights.remove, obj.data)
        self.addCleanup(bpy.data.objects.remove, obj, do_unlink=True)
        return obj

    def test_cycles_graph_matches_dxbc_radial_expression(self):
        from test_frontier_nodes import evaluate
        from carbon_eve_resources import ship
        for inner in (0, 1, 8):
            obj = self.make_light(inner)
            ship.update_attachment_light_falloff(1, 1)
            lamp = obj.data
            tree = lamp.node_tree
            ray = next(n for n in tree.nodes if n.type == 'LIGHT_PATH')
            value = tree.nodes.new('ShaderNodeValue')
            for link in list(ray.outputs['Ray Length'].links):
                tree.links.new(value.outputs[0], link.to_socket)
            strength = next(n for n in tree.nodes if n.type == 'EMISSION').inputs['Strength']
            for d in (.01, .5, 1, 2, 5, 9, 10, 12):
                value.outputs[0].default_value = d
                # Reciprocal form from DXBC 377-384, radius packed by CPU.
                attenuation = 1000 * min(max((1/(d+.001)-1/10.001)
                    / (1000-1/10.001), 0), 1)
                target = 4 * 10 * attenuation * (1-min(inner/d, .8))
                measured = evaluate(tree, socket=strength) * lamp.energy / (
                    4*math.pi**2*(d*d+lamp.shadow_soft_size**2))
                self.assertAlmostEqual(measured, target, delta=max(1e-5, target*1e-5))

    def test_eevee_anchors_and_cutoff(self):
        from carbon_eve_resources import ship
        obj = self.make_light()
        ship.update_attachment_light_falloff(1, 1)
        lamp = obj.data
        self.assertTrue(lamp.use_custom_distance)
        self.assertEqual(lamp.cutoff_distance, 10)
        for d in (1, 5):
            response = lamp.energy * (1-(d/10)**4)**2 / (
                4*math.pi**2*(d*d+lamp.shadow_soft_size**2))
            self.assertAlmostEqual(response, 4*(10-d)/(d+.001)*.2, places=5)

    def test_toggle_and_boost_preserve_authored_values(self):
        from carbon_eve_resources import ship
        obj = self.make_light()
        for enabled in (False, True):
            ship.update_attachment_lights_enabled(enabled)
            self.assertEqual(obj.hide_viewport, not enabled)
            self.assertEqual(obj.hide_render, not enabled)
        for boost in (0, .08, 1):
            ship.update_attachment_light_boost(boost)
            self.assertAlmostEqual(obj.data.energy,
                obj.data['carbon_light_power']*boost, places=3)
        self.assertEqual(obj.data['carbon_light_brightness'], 2)
        self.assertEqual(obj['carbon_light_inner_radius'], 8)

    def test_scale_squared(self):
        from carbon_eve_resources import ship
        for scale in (.01, 1, 100):
            base = ship.attachment_light_power(2, 10, 8, (1, 2, 1))[1]
            power = ship.attachment_light_power(2, 10, 8, (1, 2, 1), scale)[1]
            self.assertAlmostEqual(power/base, scale*scale)

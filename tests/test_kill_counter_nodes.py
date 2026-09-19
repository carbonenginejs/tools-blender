"""Exercise the real counter graph, including saved-tree upgrades, in Blender."""

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
class KillCounterGraphTests(unittest.TestCase):
    def setUp(self):
        from carbon_eve_resources.quad import nodes
        self.nodes = nodes
        bpy.ops.wm.read_homefile(use_empty=True)

    def coverage(self, tree, uv, count, scaling):
        values = {"UV": (*uv, 0), "killCount": count,
                  "DecalTextureScaling": (*scaling, 0)}
        def input_value(socket):
            return evaluate(socket.links[0].from_socket) if socket.is_linked else socket.default_value
        def evaluate(socket):
            node = socket.node
            if node.bl_idname == "NodeGroupInput":
                return values[socket.name]
            if node.bl_idname == "ShaderNodeSeparateXYZ":
                return input_value(node.inputs[0])["XYZ".index(socket.name)]
            a, b = (input_value(node.inputs[index]) for index in (0, 1))
            operations = {
                "MULTIPLY": lambda: a*b, "ADD": lambda: a+b,
                "SUBTRACT": lambda: a-b, "DIVIDE": lambda: a/b,
                "TRUNC": lambda: math.trunc(a), "FLOOR": lambda: math.floor(a),
                "FRACT": lambda: a-math.floor(a), "POWER": lambda: a**b,
                "GREATER_THAN": lambda: float(a>b), "LESS_THAN": lambda: float(a<b),
            }
            value = operations[node.operation]()
            return min(1, max(0, value)) if node.use_clamp else value
        output = next(node for node in tree.nodes if node.bl_idname == "NodeGroupOutput")
        return input_value(output.inputs["Coverage"])

    def test_asymmetric_count_in_all_authored_directions(self):
        tree = self.nodes.build_kill_counter_group()
        for x, y in ((0, 0), (1, 0), (0, 1), (1, 1)):
            digits = (1, 2, 3) if y == 0 else (3, 2, 1)
            for row, digit in enumerate(digits):
                expected = [1] * digit + [0] * (9-digit)
                if x:
                    expected.reverse()
                actual = [self.coverage(tree, ((col+.5)/9, (row+.5)/3), 123, (x,y))
                          for col in range(9)]
                self.assertEqual(actual, expected)

    def test_unauthored_direction_uses_depth_shader_default(self):
        tree = self.nodes.build_kill_counter_group()
        item = next(item for item in tree.interface.items_tree if item.name == "DecalTextureScaling")
        self.assertEqual(tuple(item.default_value), (1, 1, 0))

    def test_upgrade_preserves_users_links_and_count_driver(self):
        tree = self.nodes.build_kill_counter_group()
        material = bpy.data.materials.new("existing counter")
        material.use_nodes = True
        group = material.node_tree.nodes.new("ShaderNodeGroup")
        group.node_tree = tree
        group.inputs["killCount"].default_value = 123
        group.inputs["killCount"].driver_add("default_value").driver.expression = "123"
        target = material.node_tree.nodes.new("ShaderNodeMath")
        material.node_tree.links.new(group.outputs["Coverage"], target.inputs[0])
        # Legacy interface: no scaling socket or implementation version.
        item = next(item for item in tree.interface.items_tree if item.name == "DecalTextureScaling")
        tree.interface.remove(item)
        del tree["carbon_kill_counter_version"]
        upgraded = self.nodes.build_kill_counter_group()
        self.assertEqual(upgraded.as_pointer(), tree.as_pointer())
        self.assertEqual(group.inputs["killCount"].default_value, 123)
        self.assertTrue(target.inputs[0].is_linked)
        self.assertEqual(material.node_tree.animation_data.drivers[0].driver.expression, "123")
        self.assertIn("DecalTextureScaling", group.inputs)


if __name__ == "__main__":
    unittest.main()

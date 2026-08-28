"""Sharing must not leak: two areas reading one material, edited apart.

These run inside Blender, because a node group IS the thing under test -- the
sharing behaviour lives in the datablock, not in code that could be stubbed.
"""

from pathlib import Path
import sys
import unittest


ADDONS = Path(__file__).resolve().parents[1] / "addons"
if str(ADDONS) not in sys.path:
    sys.path.insert(0, str(ADDONS))

try:
    import bpy
except ImportError:                      # pragma: no cover - outside Blender
    bpy = None

from carbon_eve_resources import sof_material_nodes as nodes  # noqa: E402
from carbon_eve_resources.quad import nodes as quad_nodes  # noqa: E402


BLACK = {"diffuse": (0.0012, 0.0012, 0.0012), "fresnel": (0.034, 0.034, 0.034),
         "gloss": 0.7735}
ORANGE = {"diffuse": (0.3529, 0.0471, 0.0), "fresnel": (0.04, 0.04, 0.04),
          "gloss": 0.6}


def _quad_material(name, sockets=("Mtl1DiffuseColor", "Mtl1FresnelColor", "Mtl1Gloss")):
    """A stand-in area material: a group node with the slot's three sockets."""

    # Named the way a real one is. `quad_group_node` finds an area's shader
    # by that prefix rather than by ruling out every other kind of group.
    tree = bpy.data.node_groups.new(f"{quad_nodes.GROUP_PREFIX} {name}",
                                    "ShaderNodeTree")
    for socket in sockets:
        kind = "NodeSocketFloat" if socket.endswith("Gloss") else "NodeSocketColor"
        tree.interface.new_socket(name=socket, in_out="INPUT", socket_type=kind)
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.node_tree.nodes.clear()
    node = material.node_tree.nodes.new("ShaderNodeGroup")
    node.node_tree = tree
    return material


@unittest.skipIf(bpy is None, "needs Blender")
class MaterialGroupTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)

    def test_a_material_is_made_once_and_shared(self):
        first = nodes.material_group("black_deadstar_coated", BLACK)
        second = nodes.material_group("black_deadstar_coated", BLACK)
        self.assertIs(first, second)

    def test_an_existing_material_is_not_refilled(self):
        # Re-fetching over a group would silently undo an edit someone made.
        tree = nodes.material_group("black_deadstar_coated", BLACK)
        tree.nodes["Diffuse"].outputs[0].default_value = (1.0, 0.0, 0.0, 1.0)
        nodes.material_group("black_deadstar_coated", BLACK)
        self.assertAlmostEqual(nodes.group_values(tree)["diffuse"][0], 1.0)

    def test_values_round_trip(self):
        tree = nodes.material_group("black_deadstar_coated", BLACK)
        values = nodes.group_values(tree)
        self.assertAlmostEqual(values["gloss"], 0.7735, places=4)
        self.assertAlmostEqual(values["diffuse"][0], 0.0012, places=4)

    def test_survives_a_save(self):
        # Nothing references a fresh group, and Blender drops zero-user data.
        self.assertTrue(nodes.material_group("black_deadstar_coated", BLACK).use_fake_user)


@unittest.skipIf(bpy is None, "needs Blender")
class BindingTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self.hull = _quad_material("00 area_hull")
        self.sails = _quad_material("02 area_sails")
        self.black = nodes.material_group("black_deadstar_coated", BLACK)

    def test_links_every_socket_the_shader_has(self):
        self.assertEqual(nodes.bind_slot(self.hull, 1, self.black), 3)

    def test_a_shader_without_the_socket_is_skipped_not_forced(self):
        # quadsailsv5 does not bind what quadv5 binds.
        partial = _quad_material("partial", sockets=("Mtl1DiffuseColor",))
        self.assertEqual(nodes.bind_slot(partial, 1, self.black), 1)

    def test_two_areas_share_one_datablock(self):
        nodes.bind_slot(self.hull, 1, self.black)
        nodes.bind_slot(self.sails, 1, self.black)
        self.assertIs(nodes.bound_group(self.hull, 1),
                      nodes.bound_group(self.sails, 1))

    def test_editing_the_shared_material_reaches_both(self):
        nodes.bind_slot(self.hull, 1, self.black)
        nodes.bind_slot(self.sails, 1, self.black)
        self.black.nodes["Diffuse"].outputs[0].default_value = (1.0, 0.0, 0.0, 1.0)
        for material in (self.hull, self.sails):
            values = nodes.group_values(nodes.bound_group(material, 1))
            self.assertAlmostEqual(values["diffuse"][0], 1.0)

    def test_rebinding_one_area_leaves_the_other_alone(self):
        # The reported defect: naming the SAILS' slot 1 differently repainted
        # the hull, because writing the new values into the group they SHARE is
        # not the same as pointing the sails at a different material.
        nodes.bind_slot(self.hull, 1, self.black)
        nodes.bind_slot(self.sails, 1, self.black)
        orange = nodes.material_group("orange_fire_colorshift", ORANGE)
        nodes.bind_slot(self.sails, 1, orange)

        self.assertIs(nodes.bound_group(self.hull, 1), self.black)
        self.assertIs(nodes.bound_group(self.sails, 1), orange)
        # And the material they used to share is untouched, still describing
        # itself honestly.
        self.assertAlmostEqual(nodes.group_values(self.black)["diffuse"][0],
                               0.0012, places=4)

    def test_rebinding_leaves_one_link_per_socket(self):
        # Two sources on one input is not an error in Blender; it silently
        # keeps the older one, so the rebind would appear to do nothing.
        nodes.bind_slot(self.hull, 1, self.black)
        nodes.bind_slot(self.hull, 1, nodes.material_group("orange_fire_colorshift", ORANGE))
        quad = nodes.quad_group_node(self.hull)
        self.assertEqual(len(quad.inputs["Mtl1DiffuseColor"].links), 1)

    def test_a_private_copy_leaves_the_original(self):
        nodes.bind_slot(self.hull, 1, self.black)
        private = nodes.make_private(self.black, "hull 1")
        private.nodes["Diffuse"].outputs[0].default_value = (0.0, 1.0, 0.0, 1.0)
        self.assertAlmostEqual(nodes.group_values(self.black)["diffuse"][0],
                               0.0012, places=4)
        self.assertEqual(private["carbon_sof_material"], "custom")


if __name__ == "__main__":
    unittest.main()

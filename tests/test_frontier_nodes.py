"""Measured Frontier depth-material boundaries and Blender integration."""
import math
import random
import struct
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))
from carbon_eve_resources.quad.interface import load_family
try:
    import bpy
except ImportError:
    bpy = None


class FrontierInterfaceTests(unittest.TestCase):
    def test_only_depth_non_soppt_and_qualified_paths(self):
        family = load_family(target="frontier")
        self.assertEqual(family.tier, "sm_depth")
        for member in family.members.values():
            self.assertEqual(member.tier, "sm_depth")
            self.assertEqual(member.selected_options.get("SPACE_OBJECT_PPT_ENABLED", "SOPPT_DISABLED"), "SOPPT_DISABLED")
        path = "res:/graphics/effect/managed/space/spaceobject/v5/quad/quadv5.fx"
        self.assertIsNotNone(family.member(path.replace("quadv5.fx", "skinned_quadv5.fx")))
        self.assertIsNone(family.member("quadv5.fx"))
        self.assertIsNone(family.member(path, {"SPACE_OBJECT_PPT_ENABLED": "SOPPT_ENABLED"}))
        self.assertNotEqual(family.member(path).identity, load_family().member("quadv5.fx").identity)
        asteroid = next(m for m in family.members.values() if m.name == "asteroid")
        self.assertEqual(asteroid.constants["TriPlanarTiling"].default, (1,))
        self.assertIn("DetailMap3ConcavityMaterialIndex", asteroid.constants)
        self.assertIn("DetailMap3ConvexityMaterialIndex", asteroid.constants)
        rigid_hash = "0af988abdfcd7d16c6a028563fecabf57b32286adf8bede905362babfcbebe8d"
        self.assertEqual(family.member(path).aliases[path.replace("quadv5.fx", "skinned_quadv5.fx")]["vertexSha256"], rigid_hash)
        heat = family.member(path.replace("quadv5.fx", "quadheatv5.fx"))
        self.assertNotEqual(heat.aliases[path.replace("quadv5.fx", "skinned_quadheatv5.fx")]["vertexSha256"], rigid_hash)
        self.assertEqual(heat.aliases[path.replace("quadv5.fx", "unpackedskinned_quadheatv5.fx")]["vertexSha256"], rigid_hash)


def evaluate(tree, output_name=None, inputs=None, uv=None, socket=None, transform_scale=(1, 1, 1), float32=False):
    values = {item.name: item.default_value for item in tree.interface.items_tree
              if item.item_type == "SOCKET" and item.in_out == "INPUT"} if hasattr(tree, "interface") else {}
    values.update(inputs or {})
    memo = {}
    def read(socket):
        return output(socket.links[0].from_socket) if socket.is_linked else socket.default_value
    def output(socket):
        key = socket.as_pointer()
        if key in memo:
            return memo[key]
        node = socket.node
        kind = node.bl_idname
        if kind == "NodeGroupInput":
            result = values[socket.name]
        elif kind == "ShaderNodeGroup":
            result = evaluate(node.node_tree, socket.name, {s.name: read(s) for s in node.inputs},
                              uv=uv, transform_scale=transform_scale, float32=float32)
        elif kind == "ShaderNodeSeparateXYZ":
            result = read(node.inputs[0])["XYZ".index(socket.name)]
        elif kind == "ShaderNodeCombineXYZ":
            result = tuple(read(s) for s in node.inputs)
        elif kind == "ShaderNodeUVMap":
            result = (uv or {}).get(node.uv_map, (0, 0, 0))
        elif kind == "ShaderNodeAttribute":
            result = (uv or {}).get(node.attribute_name, (0, 0, 0))
        elif kind == "ShaderNodeTexImage":
            assert node.interpolation == "Closest" and node.image.colorspace_settings.name == "Non-Color"
            u, v, _ = read(node.inputs["Vector"])
            width, height = node.image.size
            x, y = max(0, min(width-1, math.floor(u*width))), max(0, min(height-1, math.floor(v*height)))
            texel = node.image.pixels[(y*width+x)*4:(y*width+x)*4+4]
            result = texel[3] if socket.name == "Alpha" else tuple(texel[:3])
        elif kind == "ShaderNodeVertexColor":
            color = (uv or {}).get(node.layer_name, (0, 0, 0, 1))
            result = color[3] if socket.name == "Alpha" else color[:3]
        elif kind == "ShaderNodeVectorTransform":
            vector = read(node.inputs[0])
            result = tuple(x / scale if node.convert_from == "WORLD" else x * scale
                           for x, scale in zip(vector, transform_scale))
        elif kind == "ShaderNodeMix":
            factor = read(node.inputs[0])
            result = tuple(a * (1-factor) + b * factor
                           for a, b in zip(read(node.inputs[4]), read(node.inputs[5])))
        elif kind == "ShaderNodeMath":
            a, b = (read(node.inputs[i]) for i in (0, 1))
            ops = {"ADD": lambda: a+b, "SUBTRACT": lambda: a-b,
                "MULTIPLY": lambda: a*b, "DIVIDE": lambda: a/b if b else 0,
                "POWER": lambda: a**b if a >= 0 else 0,
                "LESS_THAN": lambda: float(a<b), "GREATER_THAN": lambda: float(a>b),
                "MINIMUM": lambda: min(a,b), "MAXIMUM": lambda: max(a,b),
                "ABSOLUTE": lambda: abs(a), "SQRT": lambda: math.sqrt(max(a,0)),
                "SINE": lambda: math.sin(a),
                "TRUNC": lambda: math.trunc(a), "MODULO": lambda: a % b,
                "FLOOR": lambda: math.floor(a), "LOGARITHM": lambda: math.log(a, b),
                "COMPARE": lambda: float(abs(a-b) <= read(node.inputs[2]))}
            result = ops[node.operation]()
        elif kind == "ShaderNodeVectorMath":
            a = tuple(read(node.inputs[0]))[:3]
            b = tuple(read(node.inputs[1]))[:3]
            ops = {"ADD": lambda: tuple(x+y for x,y in zip(a,b)),
                "SUBTRACT": lambda: tuple(x-y for x,y in zip(a,b)),
                "MULTIPLY": lambda: tuple(x*y for x,y in zip(a,b)),
                "MINIMUM": lambda: tuple(min(x,y) for x,y in zip(a,b)),
                "MAXIMUM": lambda: tuple(max(x,y) for x,y in zip(a,b)),
                "SCALE": lambda: tuple(x*read(node.inputs["Scale"]) for x in a),
                "LENGTH": lambda: math.sqrt(sum(x*x for x in a)),
                "NORMALIZE": lambda: tuple(x / math.sqrt(sum(y*y for y in a)) for x in a),
                "DOT_PRODUCT": lambda: sum(x*y for x,y in zip(a,b))}
            ops["CROSS_PRODUCT"] = lambda: (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
            result = ops[node.operation]()
        else:
            raise AssertionError(f"Unexpected node in material output: {kind}")
        if float32 and isinstance(result, (float, int)):
            result = struct.unpack("<f", struct.pack("<f", result))[0]
        elif float32 and isinstance(result, tuple):
            result = tuple(struct.unpack("<f", struct.pack("<f", v))[0] for v in result)
        memo[key] = result
        return result
    if socket is not None:
        return read(socket)
    node = next(n for n in tree.nodes if n.bl_idname == "NodeGroupOutput")
    return read(node.inputs[output_name])


@unittest.skipIf(bpy is None, "needs Blender")
class FrontierGraphTests(unittest.TestCase):
    def setUp(self):
        from carbon_eve_resources.quad import frontier
        self.frontier = frontier
        self.family = load_family(target="frontier")

    def member(self, name, variant="SOT_OPAQUE"):
        return next(m for m in self.family.members.values() if m.name == name
                    and m.selected_options.get("SPACE_OBJECT_TRANSPARENCY", variant) == variant)

    def test_turret_material_quadrants_use_uv1_and_strict_half_boundary(self):
        # Isolate quadrant selection from the independently qualified dirt hue layer.
        member = self.member("turret")
        existing = bpy.data.node_groups.get(f"CarbonShader {member.identity}")
        if existing is not None:
            bpy.data.node_groups.remove(existing)
        with patch.object(self.frontier.Graph, "hue", lambda self, color, *args: color):
            tree = self.frontier.build_group(member)
        self.addCleanup(lambda: bpy.data.node_groups.remove(tree))
        inputs = {"AtlasAO.x": 0, "AtlasCurvature.x": 0,
                  "DirtData1.y": 0, "DirtData2.y": 0}
        colors = ((.1, .2, .3, 1), (.3, .4, .5, 1),
                  (.5, .6, .7, 1), (.7, .8, .9, 1))
        for index, color in enumerate(colors, 1):
            inputs[f"Mtl{index}BaseColor"] = color
        for uv, expected in zip(((.49, .49, 0), (.5, .49, 0),
                                 (.49, .5, 0), (.5, .5, 0)), colors):
            actual = evaluate(tree, "Albedo", inputs, uv={"gr2_texcoord1": uv})
            for a, b in zip(actual[:3], expected[:3]):
                self.assertAlmostEqual(a, b, places=5)

    def test_heat_field_against_depth_equations(self):
        from carbon_eve_resources.quad import nodes
        member = self.member("fxheatv5")
        tree = bpy.data.node_groups.new("Heat field qualification", "ShaderNodeTree")
        self.addCleanup(lambda: bpy.data.node_groups.remove(tree))
        tree.interface.new_socket(name="Heat", in_out="OUTPUT", socket_type="NodeSocketFloat")
        for constant in member.constants:
            for label, kind, default in self.frontier.constant_sockets(member, constant):
                nodes._socket(tree, label, kind, default=default)
        for label in ("P", "N", "V", "Sun"):
            nodes._socket(tree, label, "NodeSocketVector", default=(0, 0, 1))
        for label in ("Distance", "Radius", "Time", "Noise", "AO", "Curvature", "Grunge"):
            nodes._socket(tree, label, "NodeSocketFloat", default=0)
        graph = self.frontier.Graph(tree, member)
        get = graph.input.outputs.__getitem__
        graph.bind(graph.heat_field(*(get(label) for label in
            ("P", "Distance", "N", "V", "Sun", "Radius", "Time", "Noise", "AO", "Curvature", "Grunge"))),
            graph.output.inputs["Heat"])
        rng = random.Random(812)
        clamp = lambda x: max(0, min(x, 1))
        unit = lambda v: tuple(x / math.sqrt(sum(y*y for y in v)) for x in v)
        dot = lambda a, b: sum(x*y for x, y in zip(a, b))
        observed = []
        for case in range(100):
            shape = [rng.uniform(-.3, 1.3) for _ in range(4)]
            surface = [2, rng.random(), rng.uniform(-1, 50), rng.random()]
            output = [1, rng.uniform(-.5, .5), rng.uniform(-.2, 1.2), rng.random()]
            flow = [rng.uniform(-1, 1) for _ in range(4)] if case % 2 else [0, 0, 0, 2]
            solar = [rng.uniform(-.2, 1.2) for _ in range(4)]
            shimmer = [1, rng.random(), rng.random(), rng.random()]
            heat_range = [rng.random(), rng.uniform(-.1, 2), 0, 0]
            growth = rng.uniform(-.2, 1.5)
            p = tuple(rng.uniform(-2, 2) for _ in range(3))
            n, v, sun = (unit(tuple(rng.uniform(-1, 1) for _ in range(3))) for _ in range(3))
            distance, radius, time = rng.uniform(0, 30), rng.uniform(-1, 5), rng.uniform(0, 10)
            noise, ao, curvature, grunge = (rng.random() for _ in range(4))
            inputs = dict(P=p, N=n, V=v, Sun=sun, Distance=distance, Radius=radius,
                          Time=time, Noise=noise, AO=ao, Curvature=curvature, Grunge=grunge, Growth=growth)
            for name, values in (("HeatShape", shape), ("HeatSurface", surface), ("HeatOutput", output),
                                 ("HeatFlow", flow), ("HeatSolar", solar), ("HeatShimmer", shimmer), ("HeatRange", heat_range)):
                inputs.update({f"{name}.{lane}": value for lane, value in zip("xyzw", values)})
            g = clamp((growth-clamp(output[2]))/max(1-clamp(output[2]), .001))
            s = clamp(shape[0]+(1-ao)*shape[1]+clamp(curvature)*shape[2])
            s = clamp(s+(grunge-.5)*clamp(1-distance/max(surface[2], 1))*surface[1])
            phase = noise*6.283199787+p[1]*shimmer[1]-time*shimmer[3]*1.382303953
            flicker = .65*math.sin(phase)+.35*math.sin(phase*2.369999886+noise*11)
            width = max(shape[3], .001)
            threshold = 1+width-g*(1+2*width)+output[1]*flicker
            if math.sqrt(dot(flow[:3], flow[:3])) > .0001:
                threshold += (1-clamp(.5*(dot(p, flow[:3])+flow[3])/max(radius, .0001)+.5))*output[3]
            exposure = clamp((dot(n, sun)+solar[1])/max(1+solar[1], .001))
            exposure = clamp(solar[2])+(1-clamp(solar[2]))*exposure
            threshold += (1-exposure)*heat_range[0]
            coverage = clamp((s-threshold+width)/(2*width))
            coverage = coverage*coverage*(3-2*coverage)
            expected = (.35+(max(heat_range[1], .01)-.35)*g)*s*coverage
            expected *= (1+(exposure-1)*clamp(solar[0]))*(1+shimmer[2]*output[1]*flicker)
            expected *= 1+(1-clamp(dot(n, v)))**2*surface[3]
            expected = clamp(expected) if g > 0 else 0
            actual = evaluate(tree, "Heat", inputs)
            observed.append(actual)
            self.assertAlmostEqual(actual, expected, places=6, msg=f"heat case {case}")
        self.assertTrue(any(0 < value < 1 for value in observed))
        self.assertIn(0, observed)

    def test_area_import_connects_heat_context_and_authored_growth(self):
        from carbon_eve_resources.quad.materials import build_area_material
        member = self.member("fxheatv5")
        area = {"name": "Heat", "effect": {"effectFilePath": member.effect_path,
                "constParameters": [{"name": "Growth", "value": [.6]}], "resources": []}}
        material, problem = build_area_material(area, self.family, {}, 0,
            heat_context={"radius": 84, "sun_direction": (0, -1, 0)})
        self.assertIsNone(problem)
        self.addCleanup(lambda: bpy.data.materials.remove(material))
        group = next(n for n in material.node_tree.nodes if n.bl_idname == "ShaderNodeGroup")
        self.assertAlmostEqual(group.inputs["Growth"].default_value, .6)
        self.assertEqual(group.inputs["shipRadius"].default_value, 84)
        self.assertEqual(tuple(group.inputs["SunDirection"].default_value), (0, -1, 0))
        self.assertTrue(material["carbon_fx_vertex_view"])
        missing, problem = build_area_material(area, self.family, {}, 0)
        self.assertIsNone(missing)
        self.assertIn("ship radius", problem)
        area["effect"]["resources"] = [{"name": "NoiseMap", "resourcePath": "res:/bound.dds"}]
        missing, problem = build_area_material(area, self.family, {}, 0,
            heat_context={"radius": 84, "sun_direction": (0, -1, 0)})
        self.assertIsNone(missing)
        self.assertIn("3D volume", problem)

    def test_heat_overlay_preserves_base_surface_and_editor_identity(self):
        from carbon_eve_resources.quad.materials import build_heat_area_material
        base = bpy.data.materials.new("Heat overlay base")
        base.use_nodes = True
        base["carbon_effect_identity"] = "base effect"
        base["carbon_area"] = "opaque hull"
        self.addCleanup(lambda: bpy.data.materials.remove(base))
        member = self.member("fxheatv5")
        area = {"name": "Heat", "effect": {"effectFilePath": member.effect_path}}
        combined, problem = build_heat_area_material(area, member, {}, 0,
            {"radius": 84, "sun_direction": (0, -1, 0)}, base_material=base)
        self.assertIsNone(problem)
        self.addCleanup(lambda: bpy.data.materials.remove(combined))
        self.assertEqual(combined["carbon_effect_identity"], "base effect")
        self.assertEqual(combined["carbon_area"], "opaque hull")
        self.assertEqual(combined["carbon_heat_effect_identity"], member.identity)
        self.assertEqual(combined.surface_render_method, base.surface_render_method)
        output = next(n for n in combined.node_tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial")
        addition = output.inputs["Surface"].links[0].from_node
        self.assertEqual(addition.bl_idname, "ShaderNodeAddShader")
        self.assertEqual(addition.inputs[0].links[0].from_node.bl_idname, "ShaderNodeBsdfPrincipled")
        self.assertEqual(addition.inputs[1].links[0].from_node.bl_idname, "ShaderNodeEmission")
        original = next(n for n in base.node_tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial")
        self.assertEqual(original.inputs["Surface"].links[0].from_node.bl_idname, "ShaderNodeBsdfPrincipled")

    def test_ship_heat_overlay_only_changes_its_authored_index_group(self):
        import tempfile
        from carbon_eve_resources import ship
        mesh = bpy.data.meshes.new("Heat area geometry")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        obj = bpy.data.objects.new("Heat area hull", mesh)
        bpy.context.collection.objects.link(obj)
        base = bpy.data.materials.new("Shared base")
        base.use_nodes = True
        for _ in range(3):
            mesh.materials.append(base)
        obj.shape_key_add(name="Basis")
        morph = obj.shape_key_add(name="Animated morph")
        morph.data[0].co.z = 2
        morph.value = .5
        original_keys = mesh.shape_keys
        member = self.member("fxheatv5")
        document = {"boundingSphereRadius": 84, "mesh": {
            "_type": "Tr2Mesh", "geometryResPath": "res:/hull.cmf",
            "opaqueAreas": [{"index": 0, "count": 3, "effect": {
                "effectFilePath": self.member("simplepbr").effect_path}}],
            "transparentAreas": [{"index": 1, "count": 1, "name": "Heat",
                "effect": {"effectFilePath": member.effect_path}}]}}
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "hull.cmf"
                path.write_bytes(b"geometry supplied by fixture")
                with patch.object(ship, "import_geometry", return_value=[obj]), \
                     patch.object(ship, "build_area_material", return_value=(base, None)):
                    self.assertFalse(ship.FRONTIER_THERMAL_ENABLED)
                    ship.assemble("", "", clear=False, document=document,
                        resources={"res:/hull.cmf": str(path)}, family=self.family)
                    self.assertEqual(tuple(mesh.materials), (base, base, base))
                    with patch.object(ship, "FRONTIER_THERMAL_ENABLED", True):
                        result = ship.assemble("", "", clear=False, document=document,
                            resources={"res:/hull.cmf": str(path)}, family=self.family)
            self.assertEqual(result, obj)
            self.assertEqual(mesh.materials[0], base)
            self.assertEqual(mesh.materials[2], base)
            self.assertNotEqual(mesh.materials[1], base)
            self.assertEqual(mesh.materials[1]["carbon_heat_effect_identity"], member.identity)
            self.assertEqual(mesh.shape_keys, original_keys)
            self.assertEqual(morph.value, .5)
            self.assertEqual(morph.data[0].co.z, 2)
        finally:
            materials = set(mesh.materials)
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(mesh)
            for material in materials:
                bpy.data.materials.remove(material)

    def test_heat_wiring_uses_authored_grunge_coordinates_and_rejects_bound_volume(self):
        member = self.member("fxheatv5")
        tree = self.frontier.build_heat_group(member, ((0, 0, 0, 1), (1, 1, 1, 1)))
        material = bpy.data.materials.new("Heat input qualification")
        material.use_nodes = True
        material.node_tree.nodes.clear()
        group = material.node_tree.nodes.new("ShaderNodeGroup")
        group.node_tree = tree
        image = bpy.data.images.new("Heat grunge", width=2, height=2)
        image.colorspace_settings.name = "Non-Color"
        self.addCleanup(lambda: bpy.data.node_groups.remove(tree))
        self.addCleanup(lambda: bpy.data.images.remove(image))
        self.addCleanup(lambda: bpy.data.materials.remove(material))
        effect = {"resources": [{"name": "GrungeMap", "resourcePath": "res:/grunge.dds"}],
                  "constParameters": [{"name": "HeatSurface", "value": [2, 0, 4000, .5]}]}
        from carbon_eve_resources.quad import materials
        with patch.object(materials, "local_file", return_value="fixture"), patch.object(materials, "load_texture", return_value=image):
            self.frontier.wire_heat_inputs(member, effect, material, group, {}, radius=40, sun_direction=(1, 0, 0))
        self.assertEqual(group.inputs["shipRadius"].default_value, 40)
        self.assertEqual(tuple(group.inputs["SunDirection"].default_value), (1, 0, 0))
        samples = [node for node in material.node_tree.nodes if node.bl_idname == "ShaderNodeTexImage"]
        self.assertEqual(len(samples), 2)
        attrs = {"carbon_rest_position": (4, 7, 8), "gr2_texcoord0": (.2, .3, 0)}
        for node, expected in zip(samples, ((.6, 0, 0), (1.93, -2.01, 0))):
            actual = evaluate(material.node_tree, uv=attrs, socket=node.inputs["Vector"])
            for a, b in zip(actual, expected):
                self.assertAlmostEqual(a, b, places=6)
        self.assertEqual(material["carbon_heat_noise_contract"], "runtime-null-texture")
        before = len(material.node_tree.nodes)
        effect["resources"].append({"name": "NoiseMap", "resourcePath": "res:/volume.dds"})
        with self.assertRaisesRegex(ValueError, "3D volume"):
            self.frontier.wire_heat_inputs(member, effect, material, group, {}, radius=40, sun_direction=(1, 0, 0))
        self.assertEqual(len(material.node_tree.nodes), before)

    def test_combined_heat_group_keeps_additive_zero_growth_gradient(self):
        member = self.member("fxheatv5")
        samples = ((.2, .4, .8, .5), (1, .5, .1, 1))
        tree = self.frontier.build_heat_group(member, samples)
        self.addCleanup(lambda: bpy.data.node_groups.remove(tree))
        self.assertEqual(self.frontier.build_heat_group(member, samples), tree)
        values = {"SunDirection": (0, 0, 1), "WorldNormal": (0, 0, 1),
                  "VertexView": (0, 0, 1), "Growth": 0,
                  "HeatTint": (.5, 1, 2, 0), "HeatOutput.x": 3}
        self.assertEqual(evaluate(tree, "Heat", values), 0)
        for actual, expected in zip(evaluate(tree, "Emission", values), (.15, .6, 2.4)):
            self.assertAlmostEqual(actual, expected, places=6)
        self.assertEqual(evaluate(tree, "Alpha", values), 1)
        out = next(node for node in tree.nodes if node.bl_idname == "NodeGroupOutput")
        add = out.inputs["BSDF"].links[0].from_node
        self.assertEqual(add.bl_idname, "ShaderNodeAddShader")
        self.assertEqual({socket.links[0].from_node.bl_idname for socket in add.inputs},
                         {"ShaderNodeBsdfTransparent", "ShaderNodeEmission"})
        self.assertTrue(tree["carbon_external_bindings_required"])

    def test_mip_zero_source_row_srgb_decodes_before_filtering(self):
        image = bpy.data.images.new("Heat ramp row", width=2, height=2, alpha=True, float_buffer=True)
        self.addCleanup(lambda: bpy.data.images.remove(image))
        image.colorspace_settings.name = "Non-Color"
        image.pixels = [1, 0, 0, 1, 0, 1, 0, 1,
                        0, .04045, .5, .25, 1, .5, .04045, .75]
        row = self.frontier.mip0_first_row(image, srgb=True)
        self.assertAlmostEqual(row[0][0], 0)
        self.assertAlmostEqual(row[0][1], .04045 / 12.92, places=7)
        self.assertAlmostEqual(row[0][2], .21404114048223255, places=7)
        self.assertEqual((row[0][3], row[1][3]), (.25, .75))
        # Black/white interpolate to .5 in linear light, not sRGB(.5)=.214.
        self.assertEqual((row[0][0]+row[1][0])*.5, .5)
        image.colorspace_settings.name = "sRGB"
        with self.assertRaises(ValueError):
            self.frontier.mip0_first_row(image, srgb=True)

    def test_heat_gradient_clamp_alpha_and_zero_heat_emission(self):
        from carbon_eve_resources.quad import nodes
        member = self.member("fxheatv5")
        tree = bpy.data.node_groups.new("Heat gradient qualification", "ShaderNodeTree")
        self.addCleanup(lambda: bpy.data.node_groups.remove(tree))
        tree.interface.new_socket(name="Emission", in_out="OUTPUT", socket_type="NodeSocketVector")
        nodes._socket(tree, "Heat", "NodeSocketFloat", default=0)
        for name in ("HeatTint", "HeatOutput"):
            for label, kind, default in self.frontier.constant_sockets(member, name):
                nodes._socket(tree, label, kind, default=default)
        # Linear RGB, with deliberately independent alpha and sharp adjacent hues.
        samples = ((.2, .4, .8, .5), (1, 0, .1, .2), (0, .8, .4, 1), (.5, .1, 0, .7))
        graph = self.frontier.Graph(tree, member)
        graph.bind(graph.heat_emission(graph.input.outputs["Heat"], samples), graph.output.inputs["Emission"])
        lookups = [node for node in tree.nodes if node.bl_idname == "ShaderNodeTexImage"]
        self.assertEqual(len(lookups), 2)
        for lookup in lookups:
            self.assertEqual(lookup.interpolation, "Closest")
            self.assertEqual(lookup.image.alpha_mode, "CHANNEL_PACKED")
            self.assertFalse(lookup.image.use_half_precision)
        for heat in (-1, 0, .125, .2, .375, .5, .73, .875, 1, 2):
            coordinate = max(0, min(heat*len(samples)-.5, len(samples)-1))
            left = math.floor(coordinate)
            right = min(left+1, len(samples)-1)
            fraction = coordinate-left
            ramp = [a*(1-fraction)+b*fraction for a,b in zip(samples[left], samples[right])]
            expected = tuple(ramp[i]*tint*2.5*ramp[3] for i,tint in enumerate((.5, 1, 2)))
            for tint_alpha in (0, 1):
                actual = evaluate(tree, "Emission", {"Heat": heat, "HeatTint": (.5, 1, 2, tint_alpha), "HeatOutput.x": 2.5})
                for value, target in zip(actual, expected):
                    self.assertAlmostEqual(value, target, places=6)
        # A zero field still samples the first gradient texel: it is not forced black.
        self.assertGreater(evaluate(tree, "Emission", {"Heat": 0, "HeatOutput.x": 1})[0], 0)

    def test_heat_sun_quaternion_order_and_zero_guard(self):
        from mathutils import Quaternion, Vector
        rng = random.Random(51)
        cases = [((0, 0, 0, 0), (0, 0, 0, 1)),
                 ((0, 0, 0, 1), (0, 0, 0, 0)),
                 ((.00001, 0, 0, 0), (0, 0, 0, 1)),
                 ((.001, 0, 0, 0), (0, 0, 0, 1))]
        cases += [(tuple(rng.uniform(-2, 2) for _ in range(4)),
                   tuple(rng.uniform(-2, 2) for _ in range(4))) for _ in range(12)]
        sun = Vector((.2, -.7, .4)).normalized()
        for current, lagged in cases:
            tree = bpy.data.node_groups.new("Heat sun qualification", "ShaderNodeTree")
            try:
                tree.interface.new_socket(name="Sun", in_out="OUTPUT", socket_type="NodeSocketVector")
                graph = self.frontier.Graph(tree, None)
                graph.bind(graph.heat_sun(tuple(sun), current, lagged), graph.output.inputs["Sun"])
                expected = sun
                if min(sum(x*x for x in current), sum(x*x for x in lagged)) >= 1e-6:
                    c = Quaternion((current[3], *current[:3])).normalized()
                    l = Quaternion((lagged[3], *lagged[:3])).normalized()
                    expected = ((c @ l.conjugated()) @ sun).normalized()
                actual = Vector(evaluate(tree, "Sun"))
                self.assertLess((actual-expected).length, 1e-6)
            finally:
                bpy.data.node_groups.remove(tree)

    def test_simple_selector_boundaries_and_independent_metallic_lane(self):
        tree = self.frontier.build_group(self.member("simplepbr"))
        values = {"AlbedoMap": (.5, .5, .5), "RoughnessMap": (.5, 0, 0),
                  "GlowMap": (.25, .9, .8), "GlowColor": (2, 3, 4)}
        for index in range(1, 5):
            values[f"Mtl{index}BaseColor"] = (.2 * index, 0, 0)
            values[f"Mtl{index}GeneralData.x"] = .1 * index
            values[f"Mtl{index}GeneralData.y"] = .2 * index
        for selector, slot in ((0, 1), (.249, 1), (.25, 2), (.5, 3), (.75, 4), (1, 4)):
            values["MaterialIndexMap"] = (selector, 0, 0)
            self.assertAlmostEqual(evaluate(tree, "Albedo", values)[0], .1 * slot, places=6)
            self.assertAlmostEqual(evaluate(tree, "Roughness", values), .1 * slot, places=6)
            self.assertAlmostEqual(evaluate(tree, "Metallic", values), .2 * slot, places=6)
        self.assertEqual(evaluate(tree, "Emission", values), (.5, .75, 1))

    def test_clip_uses_separate_paint_sample_without_changing_material_paint(self):
        tree = self.frontier.build_group(self.member("quadv5", "SOT_CLIP"))
        values = {"PaintMaskMap": (.8, 0, 0), "PaintClipMap": (0, 0, 0)}
        self.assertEqual(evaluate(tree, "Alpha", values), 1)
        values["PaintClipMap"] = (1, 0, 0)
        self.assertEqual(evaluate(tree, "Alpha", values), 0)
        self.assertEqual(evaluate(tree, "Roughness", values), evaluate(tree, "Roughness", {**values, "PaintClipMap": (0,0,0)}))

    def test_simple_clip_and_structure_untiled_clip(self):
        simple = self.frontier.build_group(self.member("simplepbr", "SOT_CLIP"))
        self.assertEqual(evaluate(simple, "Alpha", {"AlphaMaskMap": (.49,0,0)}), 0)
        self.assertEqual(evaluate(simple, "Alpha", {"AlphaMaskMap": (.51,0,0)}), 1)
        structure = self.frontier.build_group(self.member("structure", "SOT_CLIP"))
        self.assertEqual(evaluate(structure, "Alpha", {"PaintClipMap": (.49,0,0)}), 1)
        self.assertEqual(evaluate(structure, "Alpha", {"PaintClipMap": (.51,0,0)}), 0)

    def test_organic_clip_requires_both_paint_samples(self):
        tree = self.frontier.build_group(self.member("asteroidv5", "SOT_CLIP"))
        for original, selected, expected in ((0, 0, 1), (1, 0, 0), (0, 1, 0), (1, 1, 0)):
            self.assertEqual(evaluate(tree, "Alpha", {
                "PaintMaskMap": (original, 0, 0), "PaintClipMap": (selected, 0, 0)}), expected)

    def test_every_supported_depth_variant_builds_and_binds_packed_lanes(self):
        from carbon_eve_resources.quad.materials import build_area_material
        for member in self.family.members.values():
            if member.name not in self.frontier.SUPPORTED:
                continue
            material, problem = build_area_material({"effect": {
                "effectFilePath": member.effect_path, "options": dict(member.selected_options),
                "constParameters": [{"name": "Mtl1GeneralData", "value": [.21, .73, 0, 0]},
                                    {"name": "BaseColor", "value": [.2, .3, .4, .37]}]}}, self.family, {}, 0)
            self.assertIsNone(problem)
            group = next(n for n in material.node_tree.nodes if n.bl_idname == "ShaderNodeGroup" and n.node_tree.name.startswith("CarbonShader"))
            if "Mtl1GeneralData.y" in group.inputs:
                self.assertAlmostEqual(group.inputs["Mtl1GeneralData.y"].default_value, .73, places=6)
            if member.name == "fxv5":
                self.assertAlmostEqual(group.inputs["BaseColorAlpha"].default_value, .37, places=6)
                if hasattr(material, "surface_render_method"):
                    self.assertEqual(material.surface_render_method, "BLENDED")

    def test_authored_uv_mix_and_fractional_tiling_precede_image_flip(self):
        from carbon_eve_resources.quad.materials import build_area_material
        member = self.member("quadenvironmentv5")
        material, problem = build_area_material({"effect": {
            "effectFilePath": member.effect_path, "options": dict(member.selected_options),
            "constParameters": [
                {"name": "GeneralData", "value": [0, .75, .25, 0]},
                {"name": "GeneralTiling", "value": [.5, 1.5, 0, 0]}]}}, self.family, {}, 0)
        self.assertIsNone(problem)
        uv = {"gr2_texcoord0": (.2, .3, 0), "gr2_texcoord1": (.6, .7, 0)}
        for name, expected in (("AlbedoMap", (.45, .4, 0)),
                               ("MaterialMap", (.25, .7, 0)),
                               ("DustNoiseMap", (4, -5, 0))):
            sample = next(n for n in material.node_tree.nodes if n.bl_idname == "ShaderNodeTexImage" and n.label == name)
            actual = evaluate(material.node_tree, uv=uv, socket=sample.inputs["Vector"])
            for a, b in zip(actual, expected):
                self.assertAlmostEqual(a, b, places=5)

    def test_triplanar_dust_offset_is_added_after_scale(self):
        from carbon_eve_resources.quad.materials import build_area_material
        member = self.member("quadtriplanarv5")
        material, problem = build_area_material({"effect": {
            "effectFilePath": member.effect_path,
            "constParameters": [
                {"name": "TextureTiling", "value": [2, 3, 4, 5]},
                {"name": "TextureTiling2", "value": [.5, .25, 1, 1]}]}}, self.family, {}, 0)
        self.assertIsNone(problem)
        texture = next(n for n in material.node_tree.nodes if n.bl_idname == "ShaderNodeTexImage" and n.label == "DustNoiseMapZ")
        actual = evaluate(material.node_tree, uv={"carbon_rest_position": (1, 2, 3),
                          "gr2_triplanar_normal": (0, 0, 1)}, socket=texture.inputs["Vector"])
        for a, b in zip(actual, (3.17, -4.67, 0)):
            self.assertAlmostEqual(a, b, places=5)

    def test_triplanar_normal_uses_inverse_transpose_with_mirrored_scale(self):
        tree = self.frontier.build_group(self.member("quadtriplanarv5"))
        for scale in ((2, 1, .5), (-2, 1, .5)):
            actual = evaluate(tree, "Normal", {"ProjectionNormal": (1, 1, 1),
                              "ProjectionWeights": (0, 0, 1)}, transform_scale=scale)
            inverse = [1 / value for value in scale]
            length = math.sqrt(sum(value * value for value in inverse))
            for a, b in zip(actual, inverse):
                self.assertAlmostEqual(a, b / length, places=5)

    def test_asteroid_packed_pair_matches_ieee_half_and_uint_float_roundtrip(self):
        import random
        tree = bpy.data.node_groups.new("packed material test", "ShaderNodeTree")
        for direction in ("INPUT", "OUTPUT"):
            for name in ("Roughness", "Metallic"):
                tree.interface.new_socket(name=name, in_out=direction, socket_type="NodeSocketFloat")
        graph = self.frontier.Graph(tree, self.member("asteroid"))
        pair = graph.packed_material(graph.input.outputs["Roughness"], graph.input.outputs["Metallic"])
        for name, value in zip(("Roughness", "Metallic"), pair):
            graph.bind(value, graph.output.inputs[name])
        rng = random.Random(71)
        cases = [(0, 0), (1, 1), (.4, 1), (2**-25, .5), (2**-24, .25),
                 (-.4, 1), (.5, 2**-14), (.500244140625, 1), (.500732421875, .25)]
        cases += [(rng.uniform(-1, 1), rng.random()) for _ in range(30)]
        try:
            for rough, metal in cases:
                rough, metal = [struct.unpack("<f", struct.pack("<f", v))[0] for v in (rough, metal)]
                low, high = [struct.unpack("<H", struct.pack("<e", v))[0] for v in (rough, metal)]
                packed = int(struct.unpack("<f", struct.pack("<f", float(low | high << 16)))[0])
                expected = [struct.unpack("<e", struct.pack("<H", v))[0] for v in (packed & 65535, packed >> 16)]
                values = {"Roughness": rough, "Metallic": metal}
                for name, value in zip(("Roughness", "Metallic"), expected):
                    self.assertEqual(evaluate(tree, name, values, float32=True), value, (rough, metal, name))
        finally:
            bpy.data.node_groups.remove(tree)

    def test_asteroid_mip0_lookup_keeps_high_frequency_texels_and_clamps(self):
        tree = bpy.data.node_groups.new("mip zero lookup test", "ShaderNodeTree")
        tree.interface.new_socket(name="Selector", in_out="INPUT", socket_type="NodeSocketFloat")
        tree.interface.new_socket(name="Value", in_out="OUTPUT", socket_type="NodeSocketFloat")
        graph = self.frontier.Graph(tree, self.member("asteroid"))
        samples = [float(index % 2) for index in range(1024)]
        graph.bind(graph.lookup_mip0(samples, graph.input.outputs["Selector"]), graph.output.inputs["Value"])
        try:
            cached = {n.as_pointer() for n in bpy.data.node_groups if n.name.startswith("Carbon Mip0 ")}
            self.assertTrue(cached)
            graph.lookup_mip0(samples, graph.input.outputs["Selector"])
            self.assertEqual(cached, {n.as_pointer() for n in bpy.data.node_groups if n.name.startswith("Carbon Mip0 ")})
            for selector in (-.1, 0, .5 / 1024, 1.5 / 1024, 737.75 / 1024, 1023.5 / 1024, 1.1):
                x = min(max(selector * 1024 - .5, 0), 1023)
                index = math.floor(x)
                expected = samples[index] + (samples[min(index + 1, 1023)] - samples[index]) * (x - index)
                self.assertAlmostEqual(evaluate(tree, "Value", {"Selector": selector}, float32=True), expected, places=5)
        finally:
            bpy.data.node_groups.remove(tree)

    def test_asteroid_secondary_color_mask_does_not_mask_packed_material_properties(self):
        member = self.member("asteroid")
        tree = self.frontier.build_group(member, lookup_samples=(0,))
        other = self.frontier.build_group(member, lookup_samples=(1,))
        self.assertNotEqual(tree.as_pointer(), other.as_pointer())
        values = {"Mtl1BaseColor": (.2, 0, 0), "Mtl3BaseColor": (0, .4, 0),
                  "Mtl4BaseColor": (0, 0, .8), "Mtl1GeneralData.x": .5,
                  "Mtl1GeneralData.y": 0, "Mtl3GeneralData.x": 1,
                  "Mtl3GeneralData.y": 0, "Mtl4GeneralData.x": 0,
                  "Mtl4GeneralData.y": 1, "AoColor1": (1, 1, 1),
                  "AoColor2": (1, 1, 1), "DetailMap3ConvexityMaterialIndex": 2/3,
                  "DetailMap3ConcavityMaterialIndex": 1, "Detail3Map": (.5, .25, 1)}
        for thickness, expected in ((0, (.2, 0, 0)), (1, (.2, .2, .2))):
            attrs = {"Color0": (0, thickness, 0, 1)}
            for actual, wanted in zip(evaluate(tree, "Albedo", values, uv=attrs), expected):
                self.assertAlmostEqual(actual, wanted, places=5)
            self.assertAlmostEqual(evaluate(tree, "Roughness", values, uv=attrs), .5625, places=5)
            self.assertAlmostEqual(evaluate(tree, "Metallic", values, uv=attrs), .25, places=5)
            self.assertEqual(evaluate(tree, "Alpha", values, uv=attrs), 1)

    def test_asteroid_material_reads_top_lookup_row_and_keys_group_by_texels(self):
        import tempfile
        from unittest.mock import patch
        from carbon_eve_resources.quad import materials
        member = self.member("asteroid")
        image = bpy.data.images.new("asteroid lookup rows", width=4, height=2, float_buffer=True)
        image.colorspace_settings.name = "Non-Color"
        top = (0, .25, .75, 1)
        image.pixels[:] = [lane for r in ((1,) * 4 + top) for lane in (r, 0, 0, 1)]
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "lookup.dds"
                path.write_bytes(b"test")
                with patch.object(materials, "load_texture", return_value=image), patch.object(self.frontier, "build_group", wraps=self.frontier.build_group) as builder:
                    material, problem = materials.build_area_material({"effect": {
                        "effectFilePath": member.effect_path,
                        "resources": [{"name": "MaterialLookupGradient", "resourcePath": "res:/lookup.dds"}]}},
                        self.family, {"res:/lookup.dds": str(path)}, 0)
                self.assertIsNone(problem)
                self.assertEqual(builder.call_args.kwargs["lookup_samples"], top)
                group = next(n for n in material.node_tree.nodes if n.bl_idname == "ShaderNodeGroup" and n.node_tree.name.startswith("CarbonShader"))
                expected = self.frontier.build_group(member, lookup_samples=top)
                self.assertEqual(group.node_tree.as_pointer(), expected.as_pointer())
                bpy.data.materials.remove(material)
        finally:
            bpy.data.images.remove(image)

    def test_asteroid_triplanar_normal_uses_direct_world_transform(self):
        tree = self.frontier.build_group(self.member("asteroid"))
        attrs = {"Color0": (0, 0, 1, 1), "gr2_tangent": (1, 0, 0),
                 "gr2_binormal": (0, 1, 0), "gr2_normal": (0, 0, 1),
                 "gr2_triplanar_normal": (2**-.5, 0, 2**-.5)}
        actual = evaluate(tree, "Normal", {"TriPlanarIntensity": 0}, uv=attrs, transform_scale=(2, 1, .5))
        expected = (2 / math.sqrt(4.25), 0, .5 / math.sqrt(4.25))
        for a, b in zip(actual, expected):
            self.assertAlmostEqual(a, b, places=6)


if __name__ == "__main__":
    unittest.main()

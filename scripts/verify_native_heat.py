"""Render native haze off/on and at two times in Eevee and Cycles.

Run with Blender --background --factory-startup --python-exit-code 1 --python
scripts/verify_native_heat.py. This checks the deliberate native approximation,
not Frontier pixel parity. Temporary EXRs are removed after comparison.
"""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))
import bpy
from carbon_eve_resources.quad import native_heat, frontier
from carbon_eve_resources.quad.interface import load_family

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
bpy.ops.mesh.primitive_plane_add(size=4)
source = bpy.context.object
base = bpy.data.materials.new("Reference grid")
base.use_nodes = True
base.node_tree.nodes.clear()
graph = frontier.Graph(base.node_tree, None, False)
uv = graph.nodes.new("ShaderNodeTexCoord")
checker = graph.nodes.new("ShaderNodeTexChecker")
checker.inputs["Scale"].default_value = 32
checker.inputs["Color1"].default_value = (.04, .08, .13, 1)
checker.inputs["Color2"].default_value = (.55, .72, .85, 1)
graph.bind(uv.outputs["UV"], checker.inputs["Vector"])
emission = graph.nodes.new("ShaderNodeEmission")
graph.bind(checker.outputs["Color"], emission.inputs["Color"])
output = graph.nodes.new("ShaderNodeOutputMaterial")
graph.bind(emission.outputs[0], output.inputs["Surface"])
source.data.materials.append(base)
member = next(m for m in load_family(target="frontier").members.values()
              if m.name == "fxheatdistortionv5")
native_heat.attach(source, source, {"index": 0, "effect": {
    "effectFilePath": member.effect_path}}, member, {}, 3)
source[native_heat.WIDTH] = .1
bpy.ops.object.camera_add(location=(0, 0, 5))
scene.camera = bpy.context.object
scene.camera.data.type = "ORTHO"
scene.camera.data.ortho_scale = 4.5
scene.render.resolution_x = scene.render.resolution_y = 128
scene.render.resolution_percentage = 100
scene.eevee.use_raytracing = True
scene.eevee.ray_tracing_method = "SCREEN"
scene.eevee.taa_render_samples = 64
scene.cycles.samples = 64
scene.cycles.use_denoising = False
scene.render.image_settings.file_format = "OPEN_EXR"
scene.render.image_settings.color_depth = "32"

with tempfile.TemporaryDirectory(prefix="carbon-native-heat-") as directory:
    for engine in ("BLENDER_EEVEE", "CYCLES"):
        scene.render.engine = engine
        pixels = []
        for label, strength, frame in (("off", 0., 1), ("on", 3., 1), ("later", 3., 30)):
            source[native_heat.STRENGTH] = strength
            source.update_tag()
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            scene.render.filepath = str(Path(directory) / f"{engine}-{label}.exr")
            bpy.ops.render.render(write_still=True)
            image = bpy.data.images.load(scene.render.filepath, check_existing=False)
            values = image.pixels[:]
            pixels.append([values[(y * 128 + x) * 4 + c]
                           for y in range(20, 108) for x in range(20, 108) for c in range(3)])
            bpy.data.images.remove(image)
        effect = sum(abs(a-b) for a, b in zip(pixels[0], pixels[1])) / len(pixels[0])
        animation = sum(abs(a-b) for a, b in zip(pixels[1], pixels[2])) / len(pixels[1])
        print("NATIVE_HEAT", engine, "off/on mean difference", effect,
              "animation mean difference", animation, flush=True)
        assert effect > .0001, (engine, "No visible refraction")
        assert animation > .0001, (engine, "No animated change")

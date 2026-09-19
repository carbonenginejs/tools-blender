"""Render kill counts 1/10/100/123 in all four authored grid directions.

Run with Blender --background --factory-startup --python-exit-code 1
--python scripts/verify_kill_counter.py. Both Cycles and Eevee must match the
decimal digits at every cell center. This exercises the actual shader graph,
independently of the Python node evaluator used by unit tests.
"""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))
import bpy
from carbon_eve_resources.quad.nodes import build_kill_counter_group

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
counts = (1, 10, 100, 123)
directions = ((0, 0), (1, 0), (0, 1), (1, 1))
group = build_kill_counter_group()
for row, (sx, sy) in enumerate(directions):
    for column, count in enumerate(counts):
        material = bpy.data.materials.new(f"Count {count} direction {sx},{sy}")
        material.use_nodes = True
        tree = material.node_tree
        tree.nodes.clear()
        shader = tree.nodes.new("ShaderNodeGroup")
        shader.node_tree = group
        shader.inputs["killCount"].default_value = count
        shader.inputs["DecalTextureScaling"].default_value = (sx, sy, 0)
        uv = tree.nodes.new("ShaderNodeTexCoord")
        tree.links.new(uv.outputs["UV"], shader.inputs["UV"])
        emission = tree.nodes.new("ShaderNodeEmission")
        tree.links.new(shader.outputs["Coverage"], emission.inputs["Color"])
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        tree.links.new(emission.outputs[0], output.inputs["Surface"])
        bpy.ops.mesh.primitive_plane_add(size=2, location=(column * 10, row * 4, 0))
        plane = bpy.context.object
        plane.scale = (4.5, 1.5, 1)
        plane.data.materials.append(material)

bpy.ops.object.camera_add(location=(15, 6, 20))
scene = bpy.context.scene
scene.camera = bpy.context.object
scene.camera.data.type = "ORTHO"
scene.camera.data.ortho_scale = 40
scene.render.resolution_x = 800
scene.render.resolution_y = 320
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "OPEN_EXR"
scene.render.image_settings.color_depth = "32"
with tempfile.TemporaryDirectory(prefix="carbon-killmarks-") as directory:
    for engine in ("CYCLES", "BLENDER_EEVEE"):
        scene.render.engine = engine
        if engine == "CYCLES":
            scene.cycles.samples = 4
            scene.cycles.use_denoising = False
        scene.render.filepath = str(Path(directory) / (engine + ".exr"))
        bpy.ops.render.render(write_still=True)
        image = bpy.data.images.load(scene.render.filepath, check_existing=False)
        pixels = image.pixels[:]
        error = 0
        for row, (sx, sy) in enumerate(directions):
            for column, count in enumerate(counts):
                for y in range(3):
                    digit_row = y if sy else 2-y
                    digit = (count // (10 ** digit_row)) % 10
                    for x in range(9):
                        digit_column = 8-x if sx else x
                        expected = float(digit_column < digit)
                        px = column*200 + 20 + x*20
                        py = row*80 + 20 + y*20
                        actual = pixels[(py*800+px)*4]
                        error = max(error, abs(actual-expected))
        print("KILL_COUNTER_RENDER", engine, "maximum error", error, flush=True)
        assert error < .001, (engine, error)
        bpy.data.images.remove(image)

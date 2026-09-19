"""Native CMF animation reaches Blender without losing curve semantics."""
import copy
import math
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/carbon-cmf/src"))
sys.path.insert(0, str(ROOT / "addons"))
from carbon_cmf import CmfError, build_gr2_animations


def curve(dimension, values, knots=(0, 1), interpolation="Linear", value_type="Float16"):
    code = {"Float16": "e", "Float32": "f", "UInt8Norm": "B"}[value_type]
    return {"valueDimension": dimension, "knotCount": len(knots), "knotType": "Float32",
            "valueType": value_type, "interpolation": interpolation,
            "knots": list(struct.pack("<" + "f" * len(knots), *knots)),
            "values": list(struct.pack("<" + code * len(values), *values))}


def fixture():
    return {"cmfVersion": 1, "skeletons": [{"name": "rig", "bones": ["root", "child"],
            "parents": [-1, 0], "restTransforms": [
                {"position": [10, 0, 0], "rotation": [0, 0, 0, 1], "scale": [1, 1, 1]},
                {"position": [0, 2, 0], "rotation": [0, 0, 0, 1], "scale": [1, 1, 1]}]}],
            "animations": [{"name": "test", "duration": 1, "channels": [
                {"target": "root", "targetType": "BonePosition", "curveIndex": 0},
                {"target": "root", "targetType": "BoneScale", "curveIndex": 1}],
                "curves": [curve(3, [10, 0, 0, 20, 0, 0, 30, 0, 0], (0, .25, 1), "Step"),
                           curve(3, [1, 1, 1, 2, 2, 2])]}]}


class CmfAnimationViewTests(unittest.TestCase):
    def test_typed_arrays_scale_and_native_preservation(self):
        graph = fixture()
        before = copy.deepcopy(graph)
        result = build_gr2_animations(graph)
        track = result[0]["trackGroups"][0]["transformTracks"][0]
        self.assertEqual(track["position"]["knots"], [0, .25, 1])
        self.assertEqual(track["position"]["degree"], 0)
        self.assertEqual(track["scaleShear"]["controls"], [1,0,0,0,1,0,0,0,1,2,0,0,0,2,0,0,0,2])
        self.assertEqual(track["orientation"]["error"], "no curve data")
        self.assertEqual(graph, before)

    def test_raw_uint8_normalization_and_shared_curve(self):
        graph = {"animations": [{"curves": [curve(1, [0, 255], value_type="UInt8Norm")],
                 "channels": [{"target": name, "targetType": "MorphTarget", "curveIndex": 0} for name in ("A", "B")]}]}
        tracks = build_gr2_animations(graph)[0]["trackGroups"][0]["vectorTracks"]
        self.assertEqual([t["valueCurve"]["controls"] for t in tracks], [[0, 1], [0, 1]])

    def test_rotation_normalization_and_hemisphere(self):
        graph = fixture()
        graph["animations"][0]["channels"] = [{"target": "root", "targetType": "BoneRotation", "curveIndex": 0}]
        graph["animations"][0]["curves"] = [curve(4, [0, 0, 0, 2, 0, 0, 0, -2])]
        track = build_gr2_animations(graph)[0]["trackGroups"][0]["transformTracks"][0]
        self.assertEqual(track["orientation"]["controls"], [0, 0, 0, 1, 0, 0, 0, 1])

    def test_invalid_channels_and_curves_rejected(self):
        for mutation in (lambda g: g["animations"][0]["channels"].append(g["animations"][0]["channels"][0]),
                         lambda g: g["skeletons"].append(copy.deepcopy(g["skeletons"][0])),
                         lambda g: g["animations"][0]["channels"][0].update(curveIndex=9),
                         lambda g: g["animations"][0]["curves"][0].update(interpolation="Cubic"),
                         lambda g: g["animations"][0]["curves"][0].update(valueDimension=4)):
            graph = fixture()
            mutation(graph)
            with self.assertRaises(CmfError):
                build_gr2_animations(graph)


try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipUnless(bpy, "needs Blender")
class BlenderCmfAnimationTests(unittest.TestCase):
    def test_cmf_operator_forwards_animation_preference(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from carbon_eve_resources.gr2_importer import addon
        prefs = SimpleNamespace(try_unpack_tangents_default=True, apply_skinning_default=True,
            import_anims_default=True, import_scale=1, import_rot_x_deg=0,
            flip_uv_v_default=True, use_smoothing_groups_default=True, smoothing_angle_default=30,
            skip_lods_default=False, resample_anims=True, max_keys_per_bone=0,
            action_length_mode="DURATION", clamp_keys_to_duration=True, action_end_padding_frames=0,
            bone_tail_mode="LOCAL_Y")
        reports = []
        operator = SimpleNamespace(filepath="fixture.cmf", report=lambda kind, text: reports.append(text))
        with patch.object(addon.os.path, "isfile", return_value=True), patch.object(addon, "_prefs", return_value=prefs), \
             patch("carbon_cmf.read_cmf", return_value={"version": 1, "animations": [{}]}), \
             patch.object(addon, "import_gr2_json", return_value={"instance_name": "test"}) as importer:
            for enabled in (True, False):
                prefs.import_anims_default = enabled
                self.assertEqual(addon.IMPORT_SCENE_OT_carbon_cmf.execute(operator, bpy.context), {"FINISHED"})
                self.assertEqual(importer.call_args.kwargs["import_anims_flag"], enabled)
        self.assertFalse(any("pending" in report for report in reports))

    def test_morph_names_weights_and_action_ownership_without_armature(self):
        from types import SimpleNamespace
        from carbon_eve_resources.gr2_importer import addon
        from carbon_eve_resources.ship import keep_actions
        graph = {"cmfVersion": 1, "animations": [{"name": "NormalLoop", "duration": 1,
            "channels": [{"target": "Smile/Shape", "targetType": "MorphTarget", "curveIndex": 0}],
            "curves": [curve(1, [0, 2, -.5], (0, .25, 1), "Step")]}]}
        graph["animations"].insert(0, copy.deepcopy(graph["animations"][0]))
        graph["animations"][0]["name"] = "OtherClip"
        data = bpy.data.meshes.new("morph animation")
        data.from_pydata([(0,0,0), (1,0,0), (0,1,0)], [], [(0,1,2)])
        obj = bpy.data.objects.new("morph animation", data)
        bpy.context.scene.collection.objects.link(obj)
        entry = {"morphTargets": [{"name": "Smile/Shape", "dataIsDeltas": True,
                    "vertex": {"position": [1,0,0]*3}}]}
        addon._add_morph_targets(obj, entry, [0,0,0,1,0,0,0,1,0])
        actions = []
        old_fps, old_base = bpy.context.scene.render.fps, bpy.context.scene.render.fps_base
        try:
            actions = addon.import_morph_animations(graph, [(obj, entry)], "test")
            self.assertEqual(len(actions), 2)
            keys = data.shape_keys
            self.assertIs(keys.animation_data.action, actions[0])
            self.assertTrue(actions[0].use_fake_user)
            fcurve = next(addon._action_fcurves(actions[0]))
            self.assertAlmostEqual(fcurve.evaluate(7.49), 0)
            self.assertAlmostEqual(fcurve.evaluate(7.5), 2)
            self.assertAlmostEqual(fcurve.evaluate(30), -.5)
            bpy.context.scene.frame_set(15)
            evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
            self.assertAlmostEqual(evaluated.data.vertices[0].co.x, 2, places=5)
            arm = SimpleNamespace(animation_data=None)
            keep_actions(actions, [arm])
            self.assertIsNone(arm.animation_data, "shape-key action was assigned to an armature")
            bone_action = bpy.data.actions.new("test.NormalLoop")
            bone_action["carbon_animation_clip"] = "NormalLoop"
            arm.animation_data = SimpleNamespace(action=None)
            actions.append(bone_action)
            keep_actions(actions, [arm])
            self.assertIs(arm.animation_data.action, bone_action)
            self.assertIs(keys.animation_data.action, actions[1], "bone and morph selected different clips")
            # GR2 scalar tracks outside the root group are not mesh morphs.
            unrelated = {"animations": [{"name": "unrelated", "trackGroups": [{"name": "controls",
                "vectorTracks": [{"name": "Smile/Shape", "dimension": 1,
                    "valueCurve": {"format": 1, "degree": 1, "knots": [0,1], "controls": [0,1]}}]}]}]}
            self.assertEqual(addon.import_morph_animations(unrelated, [(obj, entry)], "unrelated"), [])
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(data)
            for action in actions:
                bpy.data.actions.remove(action)
            bpy.context.scene.render.fps, bpy.context.scene.render.fps_base = old_fps, old_base

    def test_exact_step_boundary_and_duplicate_times(self):
        from carbon_eve_resources.gr2_importer import addon
        for t, expected in ((-.1, 1), (0, 2), (.49, 2), (.5, 4), (.99, 4), (1, 5), (2, 5)):
            self.assertEqual(addon._eval_vec_curve([0, 0, .5, .5, 1], [[1],[2],[3],[4],[5]], t, [0], 0), [expected])

    def test_native_channels_bake_step_and_continuous_components_separately(self):
        from carbon_eve_resources.gr2_importer import addon
        graph = fixture()
        graph["animations"][0]["channels"].append({"target": "root", "targetType": "BoneRotation", "curveIndex": 2})
        graph["animations"][0]["curves"].append(curve(4,
            [lane for angle in (179, 181) for lane in (0, 0, math.sin(math.radians(angle/2)), math.cos(math.radians(angle/2)))],
            value_type="Float32"))
        scene = bpy.context.scene
        old_fps, old_base = scene.render.fps, scene.render.fps_base
        arm, info = addon.import_armature(graph, scene.collection, "cmf test", 1, 0, "LOCAL_Y")
        actions = []
        try:
            actions = addon.import_animations(graph, arm, info, "cmf test", True, 0, "DURATION", True, 0)
            self.assertEqual(len(actions), 1)
            curves = list(addon._action_fcurves(actions[0]))
            location = next(c for c in curves if c.data_path == 'pose.bones["root"].location' and c.array_index == 0)
            scale = next(c for c in curves if c.data_path == 'pose.bones["root"].scale' and c.array_index == 0)
            self.assertTrue(all(k.interpolation == "CONSTANT" for k in location.keyframe_points))
            self.assertTrue(all(k.interpolation == "LINEAR" for k in scale.keyframe_points))
            self.assertAlmostEqual(location.evaluate(7.499), 0, places=5)
            self.assertAlmostEqual(location.evaluate(7.5), 10, places=5)
            self.assertAlmostEqual(scale.evaluate(15), 1.5, places=5)
            rotations = sorted((c for c in curves if c.data_path == 'pose.bones["root"].rotation_quaternion'), key=lambda c: c.array_index)
            for index in range(1, len(rotations[0].keyframe_points)):
                dot = sum(c.keyframe_points[index-1].co.y * c.keyframe_points[index].co.y for c in rotations)
                self.assertGreater(dot, 0, "decomposed quaternion sign flipped across 180 degrees")
            child = next(c for c in curves if c.data_path == 'pose.bones["child"].location' and c.array_index == 1)
            self.assertAlmostEqual(child.evaluate(15), 0, places=5)
        finally:
            data = arm.data
            bpy.data.objects.remove(arm, do_unlink=True)
            bpy.data.armatures.remove(data)
            for action in actions:
                bpy.data.actions.remove(action)
            scene.render.fps, scene.render.fps_base = old_fps, old_base


if __name__ == "__main__":
    unittest.main()

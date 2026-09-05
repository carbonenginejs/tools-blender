"""Regression cases for the Python readers' shared CMF conversion contract."""

import math
import struct
import sys
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for package in ("carbon-cmf", "carbon-granny", "carbon-gr2"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))

from carbon_cmf import CmfError, build_cmf_from_shared, build_shared_from_cmf, read_cmf
from carbon_cmf.reader import _validate_graph_structure
from carbon_gr2 import project_cmf
from carbon_gr2.cmf import Gr2CmfError
from test_cmf import MESHLESS_ANIMATION, UNCOMPRESSED_TRIANGLE


def triangle(name="Hull", x=10):
    return {"name": name, "vertex": {"position": [x, 0, 0, x + 1, 0, 0, x, 1, 0]},
            "indices": [{"name": "main", "faces": [0, 1, 2]}]}


def rig(name="rig", bone="root"):
    return {"name": name, "bones": [{"name": bone, "parentIndex": -1}]}


def curve(dimension=1, controls=None):
    return {"format": 1, "degree": 1, "dimension": dimension, "knots": [0, 1],
            "controls": controls if controls is not None else [0, 1]}


def animation(vector_tracks=None, transform_tracks=None):
    return {"name": "test", "duration": 1, "trackGroups": [{"name": "Root",
            "vectorTracks": vector_tracks or [], "transformTracks": transform_tracks or []}]}


def project(meshes=None, skeletons=None, animations=None, **extra):
    return project_cmf({"grannyFileFormatRevision": 7, "meshes": meshes or [],
                        "skeletons": skeletons or [], "animations": animations or [], **extra})


class GeometryProjectionTests(unittest.TestCase):
    def test_absolute_and_delta_morphs_produce_absolute_cmf_positions(self):
        for deltas in (True, False):
            mesh = triangle()
            values = [1, 0, 0] * 3 if deltas else [11, 0, 0, 12, 0, 0, 11, 1, 0]
            mesh["morphTargets"] = [{"name": "SmileShape", "dataIsDeltas": deltas,
                                      "vertex": {"position": values}}]
            native = build_cmf_from_shared({"meshes": [mesh]})
            target = native["meshes"][0]["lods"][0]["morphTargets"][0]
            self.assertEqual(target["vertex"]["position"], [11, 0, 0, 12, 0, 0, 11, 1, 0])
            self.assertEqual(target["maxDisplacement"], 1)
            shared = build_shared_from_cmf(native)
            self.assertFalse(shared["meshes"][0]["morphTargets"][0]["dataIsDeltas"])
            self.assertEqual(build_cmf_from_shared(shared)["meshes"][0]["lods"][0]["morphTargets"], native["meshes"][0]["lods"][0]["morphTargets"])

    def test_sparse_morph_fills_untouched_positions_from_base(self):
        mesh = triangle()
        mesh["morphTargets"] = [{"name": "raise", "vertexIndices": [1],
                                "dataIsDeltas": True, "vertex": {"position": [0, 0, 2]}}]
        result = build_cmf_from_shared({"meshes": [mesh]})["meshes"][0]
        self.assertEqual(result["lods"][0]["morphTargets"][0]["vertex"]["position"],
                         [10, 0, 0, 11, 0, 2, 10, 1, 0])

    def test_partial_normal_only_morph_and_three_component_tangent(self):
        mesh = triangle()
        mesh["vertex"].update(normal=[0, 0, 1] * 3, tangent=[1, 0, 0, 1] * 3)
        mesh["morphTargets"] = [
            {"name": "normal", "dataIsDeltas": False, "vertex": {"normal": [0, 1, 0]}},
            {"name": "tangent", "dataIsDeltas": False, "vertex": {"tangent": [0, 1, 0] * 3}},
        ]
        result = build_cmf_from_shared({"meshes": [mesh]})["meshes"][0]
        targets = result["lods"][0]["morphTargets"]
        self.assertEqual(targets[0]["vertex"]["position"], mesh["vertex"]["position"])
        self.assertEqual(targets[0]["vertex"]["normal"], [0, 1, 0, 0, 0, 1, 0, 0, 1])
        self.assertEqual(targets[1]["vertex"]["tangent"], [0, 1, 0] * 3)
        self.assertEqual(next(item for item in result["morphTargets"]["decl"] if item["usage"] == "Tangent")["elementCount"], 3)

    def test_lower_lod_morph_inherits_metadata_but_keeps_its_own_positions(self):
        mesh = triangle()
        mesh["morphTargets"] = [{"name": "raise", "dataIsDeltas": False,
                                 "vertex": {"position": [11, 0, 0, 12, 0, 0, 11, 1, 0]}}]
        low = triangle("Hull LOD 1024", 20)
        low["morphTargets"] = [{"vertex": {"position": [21, 0, 0, 22, 0, 0, 21, 1, 0]}}]
        result = project([mesh, low])["meshes"][0]
        self.assertEqual(result["lods"][1]["morphTargets"][0]["vertex"]["position"][0], 21)

    def test_bad_position_width_and_untextured_index_range_fail(self):
        mesh = triangle()
        mesh["vertex"]["position"].append(9)
        with self.assertRaisesRegex(CmfError, "vec3"):
            build_cmf_from_shared({"meshes": [mesh]})
        for index in (-1, 3, 0.5):
            mesh = triangle()
            mesh["indices"][0]["faces"][0] = index
            with self.assertRaisesRegex(CmfError, "index"):
                build_cmf_from_shared({"meshes": [mesh]})

    def test_indexed_channels_and_density_holes(self):
        mesh = triangle()
        mesh["vertex"].update(texcoord0=[0, 0, 1, 0, 0, 1], texcoord2=[0, 0, 1, 0, 0, 1], color3=[1, 0, 0, 1] * 3)
        result = build_cmf_from_shared({"meshes": [mesh]})["meshes"][0]
        self.assertEqual([(item["usage"], item["usageIndex"]) for item in result["decl"]],
                         [("Position", 0), ("TexCoord", 0), ("TexCoord", 2), ("Color", 3)])
        self.assertEqual(result["uvDensities"], [1.4142135381698608, 0, 1.4142135381698608])
        mesh["uvDensities"] = [2, 0, 3]
        self.assertEqual(build_cmf_from_shared({"meshes": [mesh]})["meshes"][0]["uvDensities"], [2, 0, 3])

    def test_decoded_packed_channels_do_not_emit_an_unpacked_declaration(self):
        mesh = triangle()
        mesh["vertex"].update(packedTangentLegacy=[0.5, 0.75, 0.75, 0.75] * 3,
                              tangent=[1, 0, 0] * 3, normal=[0, 0, 1] * 3, binormal=[0, 1, 0] * 3)
        result = build_cmf_from_shared({"meshes": [mesh]})["meshes"][0]
        self.assertEqual([item["usage"] for item in result["decl"]], ["Position", "PackedTangentLegacy"])

    def test_exact_lod_reassembly_allows_different_area_labels(self):
        low = triangle("Hull LOD 1024", 20)
        low["indices"][0]["name"] = "lower label"
        skeleton = rig()
        result = project([low, triangle(), triangle("Hull LOD 4096", 30)], [skeleton],
                         models=[{"skeleton": skeleton, "meshBindings": [0, 1, 2, -1]}])
        self.assertEqual(len(result["meshes"]), 1)
        mesh = result["meshes"][0]
        self.assertEqual([lod["threshold"] for lod in mesh["lods"]], [0xFFFFFFFF, 4096, 1024])
        self.assertEqual(mesh["skeleton"], 0)
        self.assertEqual([area["name"] for area in mesh["areas"]], ["main"])
        self.assertEqual([lod["vertex"]["position"][0] for lod in mesh["lods"]], [10, 30, 20])
        rebuilt = build_cmf_from_shared(build_shared_from_cmf(result))["meshes"][0]
        self.assertEqual([lod["threshold"] for lod in rebuilt["lods"]], [0xFFFFFFFF, 4096, 1024])
        flattened = build_cmf_from_shared(build_shared_from_cmf(result, flatten_lods=True))
        self.assertEqual(len(flattened["meshes"]), 3)
        self.assertTrue(all(item["lods"][0]["threshold"] == 0xFFFFFFFF for item in flattened["meshes"]))

    def test_ambiguous_or_nonexact_lod_names_are_not_grouped(self):
        for names in (("Hull", "Hull LOD 4", "Hull LOD 4"), ("Hull", "Hull", "Hull LOD 4"), ("Hull", "Hull LOD4")):
            self.assertEqual(len(project([triangle(name) for name in names])["meshes"]), len(names))

    def test_lod_thresholds_and_missing_morphs_are_rejected(self):
        for threshold in (None, -1, 0xFFFFFFFF, 0.5):
            mesh = triangle()
            low = triangle()
            if threshold is not None:
                low["threshold"] = threshold
            mesh["lods"] = [triangle(), low]
            with self.assertRaises(CmfError):
                build_cmf_from_shared({"meshes": [mesh]})
        mesh = triangle()
        mesh["morphTargets"] = [{"name": "Smile", "vertex": {"position": [1, 0, 0] * 3}}]
        with self.assertRaisesRegex(CmfError, "morph"):
            project([mesh, triangle("Hull LOD 1024", 20)])

    def test_palette_selects_compatible_skeleton_and_rigid_palette_is_removed(self):
        mesh = triangle()
        mesh["boneBindings"] = [{"name": "right"}]
        wrong, right = rig("wrong", "wrong"), rig("right", "right")
        result = project([mesh], [wrong, right], models=[{"skeleton": wrong, "meshBindings": [0]}])
        self.assertEqual(result["meshes"][0]["skeleton"], 1)
        self.assertEqual(result["meshes"][0]["boneBindings"], [])
        mesh["vertex"]["blendIndice"] = [0, 0, 0, 0] * 3
        result = project([mesh], [wrong, right])
        self.assertEqual(result["meshes"][0]["boneBindings"][0]["name"], "right")
        self.assertEqual(result["meshes"][0]["vertex"]["blendWeight"], [1, 0, 0, 0] * 3)
        mesh["skeleton"] = 0
        with self.assertRaisesRegex(Gr2CmfError, "palette"):
            project([mesh], [wrong, right])

    def test_ambiguous_skeletons_fail(self):
        mesh = triangle()
        mesh["boneBindings"] = [{"name": "root"}]
        with self.assertRaisesRegex(Gr2CmfError, "unambiguous"):
            project([mesh], [rig("a"), rig("b")])

    def test_relative_shear_tolerance_and_explicit_inverse_binds(self):
        skeleton = rig()
        values = [10, 2e-6, 0, 0, 10, 0, 0, 0, 10]
        skeleton["bones"][0]["scaleShear"] = values
        inverse = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, -20, 0, 0, 1]
        skeleton["invBindTransforms"] = [inverse]
        track = {"name": "root", "scaleShear": curve(9, values * 2)}
        result = project(skeletons=[skeleton], animations=[animation(transform_tracks=[track])])
        self.assertEqual(result["skeletons"][0]["restTransforms"][0]["scale"], [10, 10, 10])
        self.assertEqual(result["skeletons"][0]["invBindTransforms"], [inverse])
        values[1] = 1e-3
        with self.assertRaisesRegex(Gr2CmfError, "shear"):
            project(skeletons=[skeleton])

    def test_only_actual_scalar_morph_tracks_are_projected_and_first_duplicate_wins(self):
        mesh = triangle()
        mesh["morphTargets"] = [{"name": "SmileShape", "vertex": {"position": [1, 0, 0] * 3}}]
        tracks = [{"name": "Smile", "dimension": 1, "valueCurve": curve()},
                  {"name": "Smile", "dimension": 1, "valueCurve": curve(1, [4, 5])},
                  {"name": "bindInverseScaleX", "dimension": 2, "valueCurve": {"error": "ignore metadata"}}]
        result = project([mesh], animations=[animation(tracks)])["animations"][0]
        self.assertEqual([(channel["target"], channel["targetType"]) for channel in result["channels"]], [("Smile", "MorphTarget")])
        self.assertEqual(struct.unpack("<2f", bytes(result["curves"][0]["values"])), (0, 1))
        tracks[0]["dimension"] = 2
        with self.assertRaisesRegex(Gr2CmfError, "dimension"):
            project([mesh], animations=[animation(tracks)])

    def test_shape_named_morph_is_not_stripped_to_empty(self):
        mesh = triangle()
        mesh["morphTargets"] = [{"name": "Shape", "vertex": {"position": [1, 0, 0] * 3}}]
        result = project([mesh], animations=[animation([{"name": "Shape", "dimension": 1, "valueCurve": curve()}])])
        self.assertEqual(result["animations"][0]["channels"][0]["target"], "Shape")

    def test_invalid_animations_and_duplicate_bone_channels_fail(self):
        for duration in (0, -1, float("nan"), float("inf")):
            with self.assertRaisesRegex(Gr2CmfError, "duration"):
                project(animations=[{**animation(), "duration": duration}])
        track = {"name": "root", "position": curve(3, [0, 0, 0, 1, 0, 0])}
        with self.assertRaisesRegex(Gr2CmfError, "duplicate"):
            project(animations=[animation(transform_tracks=[track, track])])
        track["position"]["knots"] = [1, 0]
        with self.assertRaisesRegex(Gr2CmfError, "ascending"):
            project(animations=[animation(transform_tracks=[track])])


class CmfValidationParityTests(unittest.TestCase):
    def test_zero_crc_is_not_a_validation_opt_out(self):
        data = bytearray(UNCOMPRESSED_TRIANGLE)
        struct.pack_into("<I", data, 12, 0)
        with self.assertRaisesRegex(CmfError, "CRC mismatch"):
            read_cmf(data)
        self.assertEqual(read_cmf(data, validate_crc=False)["meshes"][0]["name"], "tri")

    def test_malformed_animation_bytes_fail_before_hydration(self):
        cases = [("f", 400, 0), ("Q", 376, 0), ("Q", 424, 0), ("B", 432, 1),
                 ("f", 484, -1), ("f", 484, math.nan), ("f", 488, math.nan),
                 ("f", 248, math.nan), ("I", 444, 0)]
        for code, offset, value in cases:
            with self.subTest(offset=offset, value=value):
                data = bytearray(MESHLESS_ANIMATION)
                struct.pack_into("<" + code, data, offset, value)
                struct.pack_into("<I", data, 12, zlib.crc32(data[16:]) & 0xFFFFFFFF)
                with self.assertRaises(CmfError):
                    read_cmf(data, decode_buffers=False)

    def test_structural_geometry_failures(self):
        mutations = [
            lambda mesh: mesh.update(uvDensities=[]),
            lambda mesh: mesh["decl"][1].update(offset=0),
            lambda mesh: mesh["decl"][0].update(usage="Normal"),
            lambda mesh: mesh["lods"][0].update(threshold=1),
            lambda mesh: mesh["lods"][0]["vb"].update(stride=4),
            lambda mesh: mesh["lods"][0]["areas"][0].update(elementCount=2),
            lambda mesh: mesh["areas"][0].update(bones=[0]),
            lambda mesh: mesh.update(boneBindings=[{"name": "root", "bounds": mesh["bounds"]}]),
        ]
        for mutate in mutations:
            result = read_cmf(UNCOMPRESSED_TRIANGLE, decode_buffers=False)
            mutate(result["meshes"][0])
            with self.assertRaises(CmfError):
                _validate_graph_structure(result)

    def test_nonfinite_vertices_and_section_root_escape(self):
        data = bytearray(UNCOMPRESSED_TRIANGLE)
        vertex_offset = read_cmf(data, decode_buffers=False)["sections"][1]["offset"]
        struct.pack_into("<f", data, vertex_offset, math.nan)
        with self.assertRaisesRegex(CmfError, "non-finite"):
            read_cmf(data, validate_crc=False)
        data = bytearray(MESHLESS_ANIMATION)
        struct.pack_into("<II", data, 36, 16, 16)
        for offset in (72, 88, 104):
            struct.pack_into("<Q", data, offset, 0)
        with self.assertRaisesRegex(CmfError, "root size"):
            read_cmf(data, validate_crc=False)


try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipUnless(bpy, "needs Blender")
class BlenderMorphParityTests(unittest.TestCase):
    def test_cmf_absolute_position_reaches_shape_key_without_double_offset(self):
        sys.path.insert(0, str(ROOT / "addons"))
        from carbon_eve_resources.gr2_importer.addon import _add_morph_targets
        mesh = triangle()
        mesh["morphTargets"] = [{"name": "Smile", "dataIsDeltas": True,
                                "vertex": {"position": [1, 0, 0] * 3}}]
        shared = build_shared_from_cmf(build_cmf_from_shared({"meshes": [mesh]}))["meshes"][0]
        positions = mesh["vertex"]["position"]
        data = bpy.data.meshes.new("cmf_parity_test")
        data.from_pydata([positions[index:index + 3] for index in range(0, 9, 3)], [], [(0, 1, 2)])
        obj = bpy.data.objects.new("cmf_parity_test", data)
        try:
            self.assertEqual(_add_morph_targets(obj, shared, positions), 1)
            self.assertEqual(data.shape_keys.key_blocks["Basis"].data[0].co.x, 10)
            self.assertEqual(data.shape_keys.key_blocks["Smile"].data[0].co.x, 11)
        finally:
            bpy.data.objects.remove(obj)
            bpy.data.meshes.remove(data)


if __name__ == "__main__":
    unittest.main()

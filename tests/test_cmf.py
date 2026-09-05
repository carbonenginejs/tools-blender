import base64
import math
import struct
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "carbon-cmf" / "src"))

from carbon_cmf import (  # noqa: E402
    CmfError,
    PACKED_TANGENT,
    PACKED_TANGENT_LEGACY,
    build_shared_from_cmf,
    decode_packed_tangent,
    inspect,
    read_cmf,
)
from carbon_cmf.meshopt import decode_index_buffer, decode_vertex_buffer  # noqa: E402


# Authored triangle fixtures emitted by the runtime CMF writer, including UV density.
UNCOMPRESSED_TRIANGLE = base64.b64decode(
    "Y21mZgEAAABgAAAAsq17DBEAAAAAAAAAQAAAAAAAAABgAAAAvAEAALwBAAAAAAAAIAIAADwAAAA8AAAAFAABAGACAAAGAAAABgAAAAIAAQBoAgAAPgAAAD4AAAAAAAIAMQAAAAAAAADYAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAA2QAAAAAAAAADAAAAAAAAANEAAAAAAAAAEAAAAAAAAADRAAAAAAAAAEgAAAAAAAAAEQEAAAAAAABAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAABkBAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAD/AAAAAAAAdHJpAAAAAAAAAAADAAAAAAQAAAIMAAAAAQAAAAAAAAA8AAAAFAAAAAIAAAAAAAAABgAAAAIAAAApAAAAAAAAAAgAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAP////8AAAAAAAAAAAEAAABBAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAAAACAPwAAgD8AAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAbWFpbgAAAADzBLU/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACAPwAAAAAAAAAAAACAPwAAAAAAAAAAAACAPwAAAAAAAAAAAACAPwAAAAAAAAEAAgAAABEAAAAAAAAAIAAAAAAAAAAhAAAAAAAAAAcAAAAAAAAAGQAAAAAAAAAGAAAAAAAAAGZpeHR1cmUAcHl0aG9u"
)
COMPRESSED_TRIANGLE = base64.b64decode(
    "Y21mZgEAAABgAAAAeW02QhEAAAAAAAAAQAAAAAAAAABgAAAAvAEAALwBAAAAAAAAIAIAADEAAAA8AAAAFAABAVgCAAASAAAABgAAAAIAAQJwAgAAPgAAAD4AAAAAAAIAMQAAAAAAAADYAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAA2QAAAAAAAAADAAAAAAAAANEAAAAAAAAAEAAAAAAAAADRAAAAAAAAAEgAAAAAAAAAEQEAAAAAAABAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAABkBAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAD/AAAAAAAAdHJpAAAAAAAAAAADAAAAAAQAAAIMAAAAAQAAAAAAAAA8AAAAFAAAAAIAAAAAAAAABgAAAAIAAAApAAAAAAAAAAgAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAP////8AAAAAAAAAAAEAAABBAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAAAACAPwAAgD8AAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAbWFpbgAAAADzBLU/AAAAAKH66qr66gAA/wB/fgAAfwAA/wB/fgAAfwAAAAAAAAAAAAAAAAAAAAAAAAAAAQEAAQEAAAAAAAAA4fAAdodWZ3iphmWJaJgBaQAAAAAAAAAAEQAAAAAAAAAgAAAAAAAAACEAAAAAAAAABwAAAAAAAAAZAAAAAAAAAAYAAAAAAAAAZml4dHVyZQBweXRob24="
)
MESHLESS_ANIMATION = base64.b64decode(
    "Y21mZgEAAABAAAAAhlrRrREAAAAAAAAAIAAAAAAAAABAAAAAwAEAAMABAAAAAAAAAAIAAEAAAABAAAAAAAACAAEAAAAAAAAAAAAAAAAAAAAhAAAAAAAAAGAAAAAAAAAAAQEAAAAAAAA4AAAAAAAAAGEAAAAAAAAAAwAAAAAAAABZAAAAAAAAABAAAAAAAAAAYQAAAAAAAAAEAAAAAAAAAFkAAAAAAAAAKAAAAAAAAABxAAAAAAAAAEAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAHJpZwAAAAAAEQAAAAAAAAAEAAAAAAAAAHJvb3QAAAAA/////wAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAIA/AACAPwAAgD8AAIA/AACAPwAAAAAAAAAAAAAAAAAAAAAAAIA/AAAAAAAAAAAAAAAAAAAAAAAAgD8AAAAAAAAAAAAAAAAAAAAAAACAPzkAAAAAAAAABAAAAAAAAAAxAAAAAAAAABgAAAAAAAAAOQAAAAAAAAAoAAAAAAAAAAAAgD8AAAAAaWRsZQAAAABJ/////////wQAAAAAAAAAAAAAAAAAAAADAQAAAgAAACEAAAAAAAAACAAAAAAAAAAZAAAAAAAAABgAAAAAAAAAAAAAAAAAgD8AAAAAAAAAAAAAAAAAAIA/AAAAQAAAQEARAAAAAAAAACAAAAAAAAAAIQAAAAAAAAAEAAAAAAAAABkAAAAAAAAACAAAAAAAAABraW5kAAAAAG1lc2hsZXNz"
)
PACKED_TANGENT_MESH = base64.b64decode(
    "Y21mZgEAAABAAAAAj/IJUxEAAAAAAAAAIAAAAAAAAABAAAAAcAEAAHABAAAAAAAAsAEAABQAAAAUAAAAFAABADEAAAAAAAAA2AAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAANkAAAAAAAAADQAAAAAAAADZAAAAAAAAABAAAAAAAAAA2QAAAAAAAABIAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB/wAAAAAAAHBhY2tlZFRhbmdlbnQAAAAAAAADAAAAAAgABAQMAAAAAQAAAAAAAAAUAAAAFAAAAAAAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAP////8AAAAAAAAAAAAAAAAAAAAAAAAAAAAA/38="
)
PACKED_TANGENT_LEGACY_MESH = base64.b64decode(
    "Y21mZgEAAABAAAAAwFni1xEAAAAAAAAAIAAAAAAAAABAAAAAeAEAAHgBAAAAAAAAuAEAABQAAAAUAAAAFAABADEAAAAAAAAA2AAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAANkAAAAAAAAAEwAAAAAAAADhAAAAAAAAABAAAAAAAAAA4QAAAAAAAABIAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB/wAAAAAAAHBhY2tlZFRhbmdlbnRMZWdhY3kAAAAAAAAAAAMAAAAACQACBAwAAAABAAAAAAAAABQAAAAUAAAAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAA/////wAAAAAAAAAAAAAAAAAAAAAAgP+//7//vw=="
)
PACKED_TANGENT_BOTH_MESH = base64.b64decode(
    "Y21mZgEAAABAAAAAemoGFhEAAAAAAAAAIAAAAAAAAABAAAAAcAEAAHABAAAAAAAAsAEAABwAAAAcAAAAHAABADEAAAAAAAAA2AAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAANkAAAAAAAAABAAAAAAAAADRAAAAAAAAABgAAAAAAAAA2QAAAAAAAABIAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB/wAAAAAAAGJvdGgAAAAAAAAAAwAAAAAIAAQEDAAAAAkAAgQUAAAAAQAAAAAAAAAcAAAAHAAAAAAAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAP////8AAAAAAAAAAAAAAAAAAAAAAAAAAAAA/38AgABA/78AQA=="
)


class CmfReaderTests(unittest.TestCase):
    def test_reads_uncompressed_and_compressed_geometry_equivalently(self):
        expected_vertex = {
            "position": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
            "texcoord0": [0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
        }
        for payload in (UNCOMPRESSED_TRIANGLE, COMPRESSED_TRIANGLE):
            with self.subTest(compressed=payload is COMPRESSED_TRIANGLE):
                result = read_cmf(payload)
                mesh = result["meshes"][0]
                self.assertEqual(mesh["name"], "tri")
                self.assertEqual(mesh["vertex"]["position"], expected_vertex["position"])
                self.assertEqual(mesh["vertex"]["texcoord0"], expected_vertex["texcoord0"])
                self.assertEqual(mesh["indices"][0]["faces"], [0, 1, 2])
                self.assertEqual(result["metadata"]["entries"][0]["value"], "python")

    def test_inspects_without_decoding_buffers(self):
        summary = inspect(COMPRESSED_TRIANGLE)
        self.assertEqual(summary["version"], 1)
        self.assertEqual(summary["meshes"][0]["name"], "tri")
        self.assertEqual(summary["sections"][1]["compression"], "MeshOptimizerVertexBuffer")

    def test_validates_crc(self):
        corrupt = bytearray(UNCOMPRESSED_TRIANGLE)
        corrupt[-1] ^= 1
        with self.assertRaisesRegex(CmfError, "CRC mismatch"):
            read_cmf(corrupt)
        self.assertEqual(read_cmf(corrupt, validate_crc=False)["meshes"][0]["name"], "tri")

    def test_reads_meshless_skeleton_and_animation_documents(self):
        result = read_cmf(MESHLESS_ANIMATION)
        self.assertEqual(result["meshes"], [])
        self.assertEqual(result["skeletons"][0]["bones"], ["root"])
        self.assertEqual(result["skeletons"][0]["parents"], [0xFFFFFFFF])
        animation = result["animations"][0]
        self.assertEqual(animation["name"], "idle")
        self.assertEqual(animation["channels"][0]["targetType"], "BonePosition")
        self.assertEqual(
            struct.unpack("<6f", bytes(animation["curves"][0]["values"])),
            (0.0, 0.0, 0.0, 1.0, 2.0, 3.0),
        )

    def test_builds_shared_geometry_and_preserves_native_rig_data(self):
        document = read_cmf(UNCOMPRESSED_TRIANGLE)
        shared = build_shared_from_cmf(document)
        self.assertEqual(shared["cmfVersion"], 1)
        self.assertEqual(shared["meshes"][0]["name"], "tri")
        self.assertEqual(
            shared["meshes"][0]["vertex"]["position"],
            document["meshes"][0]["lods"][0]["vertex"]["position"],
        )
        self.assertEqual(shared["meshes"][0]["indices"][0]["faces"], [0, 1, 2])

        meshless = read_cmf(MESHLESS_ANIMATION)
        shared_meshless = build_shared_from_cmf(meshless)
        self.assertEqual(shared_meshless["meshes"], [])
        self.assertIs(shared_meshless["skeletons"][0], meshless["skeletons"][0])
        self.assertIs(shared_meshless["animations"][0], meshless["animations"][0])

    def test_can_flatten_cmf_lods_for_existing_geometry_importers(self):
        document = read_cmf(UNCOMPRESSED_TRIANGLE)
        mesh = document["meshes"][0]
        mesh["morphTargets"] = {
            "decl": [],
            "targets": [{"name": "raised", "maxDisplacement": 2.0}],
        }
        mesh["lods"][0]["morphTargets"] = [
            {"vertex": {"position": [0.0, 0.0, 2.0] * 3}}
        ]
        mesh["lods"].append(mesh["lods"][0])
        shared = build_shared_from_cmf(document, flatten_lods=True)
        self.assertEqual([mesh["name"] for mesh in shared["meshes"]], ["tri", "tri LOD 1"])
        self.assertEqual(shared["meshes"][1]["indices"][0]["faces"], [0, 1, 2])
        self.assertEqual(shared["meshes"][0]["morphTargets"][0]["name"], "raised")
        self.assertEqual(shared["meshes"][1]["morphTargets"][0]["maxDisplacement"], 2.0)
        self.assertFalse(shared["meshes"][0]["morphTargets"][0]["dataIsDeltas"])

    def test_unpacks_current_and_legacy_cmf_tangent_frames(self):
        for payload, raw_channel in (
            (PACKED_TANGENT_MESH, "packedTangent"),
            (PACKED_TANGENT_LEGACY_MESH, "packedTangentLegacy"),
        ):
            with self.subTest(channel=raw_channel):
                vertex = read_cmf(payload)["meshes"][0]["vertex"]
                self.assertEqual(len(vertex[raw_channel]), 4)
                for actual, expected in zip(vertex["normal"], (0.0, 0.0, 1.0)):
                    self.assertAlmostEqual(actual, expected, places=4)
                for actual, expected in zip(vertex["tangent"], (1.0, 0.0, 0.0)):
                    self.assertAlmostEqual(actual, expected, places=4)
                for actual, expected in zip(vertex["binormal"], (0.0, 1.0, 0.0)):
                    self.assertAlmostEqual(actual, expected, places=4)

                packed = read_cmf(payload, unpack_tangents=False)["meshes"][0]["vertex"]
                self.assertEqual(packed["normal"], [])
                self.assertEqual(packed["tangent"], [])
                self.assertEqual(len(packed[raw_channel]), 4)

    def test_current_and_legacy_tangent_modes_preserve_normal_sign(self):
        current = decode_packed_tangent((0.0, 0.0, 0.0, -1.0), PACKED_TANGENT)
        legacy = decode_packed_tangent((0.5, 0.25, 0.75, 0.25), PACKED_TANGENT_LEGACY)
        for frame in (current, legacy):
            for actual, expected in zip(frame["normal"], (0.0, 0.0, -1.0)):
                self.assertAlmostEqual(actual, expected, places=5)

    def test_tangent_edge_cases_match_native_cmf_and_gr2_null_semantics(self):
        legacy8 = decode_packed_tangent(
            (128 / 255, 191 / 255, 191 / 255, 191 / 255),
            PACKED_TANGENT_LEGACY,
        )
        for channel, expected in (
            ("normal", (0.0, 0.0, 1.0)),
            ("tangent", (1.0, 0.0, 0.0)),
            ("binormal", (0.0, 1.0, 0.0)),
        ):
            for actual, value in zip(legacy8[channel], expected):
                self.assertAlmostEqual(actual, value, delta=0.02)

        native_null = decode_packed_tangent(
            (0.0, 1.0, 0.0, 1.0),
            PACKED_TANGENT_LEGACY,
        )
        gr2_null = decode_packed_tangent(
            (0.0, 1.0, 0.0, 1.0),
            PACKED_TANGENT_LEGACY,
            zero_legacy_null=True,
        )
        self.assertTrue(native_null["isNull"])
        self.assertAlmostEqual(native_null["tangent"][2], -1.0, places=5)
        self.assertEqual(gr2_null["normal"], [0.0, 0.0, 0.0])
        self.assertEqual(gr2_null["tangent"], [0.0, 0.0, 0.0])
        self.assertEqual(gr2_null["binormal"], [0.0, 0.0, 0.0])

        overshoot = decode_packed_tangent((1.0, 1.0, 1.0, 0.5), PACKED_TANGENT)
        self.assertTrue(
            all(
                math.isfinite(value)
                for channel in ("normal", "tangent", "binormal")
                for value in overshoot[channel]
            )
        )

    def test_current_tangent_mode_takes_precedence_over_legacy(self):
        vertex = read_cmf(PACKED_TANGENT_BOTH_MESH)["meshes"][0]["vertex"]
        self.assertEqual(len(vertex["packedTangent"]), 4)
        self.assertEqual(len(vertex["packedTangentLegacy"]), 4)
        self.assertAlmostEqual(vertex["normal"][2], 1.0, places=5)

    def test_decodes_legacy_and_current_meshopt_indices(self):
        version0 = bytes(
            [0xE0, 0xF0, 0x10, 0xFE, 0xFF, 0xF0, 0x0C, 0xFF, 0x02, 0x02, 0x02,
             0x00, 0x76, 0x87, 0x56, 0x67, 0x78, 0xA9, 0x86, 0x65, 0x89, 0x68,
             0x98, 0x01, 0x69, 0x00, 0x00]
        )
        version1 = bytes(
            [0xE1, 0xF0, 0x10, 0xFE, 0x1F, 0x3D, 0x00, 0x0A, 0x00, 0x76, 0x87,
             0x56, 0x67, 0x78, 0xA9, 0x86, 0x65, 0x89, 0x68, 0x98, 0x01, 0x69,
             0x00, 0x00]
        )
        self.assertEqual(
            struct.unpack("<12H", decode_index_buffer(version0, 12, 2)),
            (0, 1, 2, 2, 1, 3, 4, 6, 5, 7, 8, 9),
        )
        self.assertEqual(
            struct.unpack("<15I", decode_index_buffer(version1, 15, 4)),
            (0, 1, 2, 2, 1, 3, 0, 1, 2, 2, 1, 5, 2, 1, 4),
        )

    def test_decodes_meshopt_vertex_v0_and_v1_xor_channels(self):
        version0 = bytes.fromhex(
            "a0013f0000005857580126000000010c00000058010800000000000000013f000000"
            "1718170126000000010c000000170108000000000000000000000000000000000000"
            "0000000000000000000000000000000000"
        )
        expected0 = bytes(
            [
                0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 44, 1, 0, 0, 0, 0, 0,
                0, 244, 1, 0, 0, 0, 0, 44, 1, 0, 0, 0, 0, 0, 0, 244, 1, 44,
                1, 44, 1, 0, 0, 0, 0, 244, 1, 244, 1,
            ]
        )
        self.assertEqual(decode_vertex_buffer(version0, 4, 12), expected0)

        version1 = bytes.fromhex(
            "a1ababfaff0002020200a5c23a00abf9570095428500ddca6d00ac1a50005c99fe00"
            "4d0c390000000000000000700000000080070c22263b8600001200"
        )
        self.assertEqual(
            struct.unpack("<16I", decode_vertex_buffer(version1, 4, 16)),
            (
                0, 112, 201818112, 2252023330,
                1, 29, 1188167680, 1600748723,
                2, 126, 1739489280, 1696368920,
                3, 155, 621084672, 1218163169,
            ),
        )


if __name__ == "__main__":
    unittest.main()

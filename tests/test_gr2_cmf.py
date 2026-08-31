import struct
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
for package in ("carbon-cmf", "carbon-granny", "carbon-gr2"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))

from carbon_gr2 import project_cmf  # noqa: E402
from carbon_gr2.json_graph import emit_json  # noqa: E402


class Gr2CmfTests(unittest.TestCase):
    def test_emitter_preserves_root_skeleton_identity_and_vector_tracks(self):
        skeleton = {
            "Name": "rig",
            "Bones": [{"Name": "root", "ParentIndex": -1, "LocalTransform": {"flags": 0}}],
        }
        file_info = {
            "Skeletons": [skeleton],
            "Models": [{"Name": "model", "Skeleton": skeleton, "MeshBindings": []}],
            "Animations": [
                {
                    "Name": "face",
                    "Duration": 1,
                    "TrackGroups": [
                        {
                            "Name": "Root",
                            "VectorTracks": [
                                {
                                    "Name": "Smile",
                                    "Dimension": 1,
                                    "ValueCurve": {
                                        "CurveData": {
                                            "CurveDataHeader_DaK32fC32f": {"Format": 1, "Degree": 1},
                                            "Knots": [0, 1],
                                            "Controls": [0, 1],
                                        }
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        result = emit_json(file_info, 7)
        self.assertIs(result["skeletons"][0], result["models"][0]["skeleton"])
        vector = result["animations"][0]["trackGroups"][0]["vectorTracks"][0]
        self.assertEqual(vector["name"], "Smile")
        self.assertEqual(vector["dimension"], 1)

    def test_projects_meshless_skeleton_and_all_vector_dimensions(self):
        skeleton = {
            "name": "rig",
            "bones": [
                {
                    "name": "root",
                    "parentIndex": -1,
                    "position": [0, 0, 0],
                    "orientation": [0, 0, 0, 1],
                    "scaleShear": [1, 0, 0, 0, 1, 0, 0, 0, 1],
                }
            ],
        }
        curve1 = {"format": 1, "degree": 1, "knots": [0, 1], "controls": [0, 1], "dimension": 1}
        curve2 = {"format": 1, "degree": 1, "knots": [0, 1], "controls": [0, 1, 2, 3], "dimension": 2}
        source = {
            "grannyFileFormatRevision": 7,
            "grannyFileSource": "meshless.gsf-animation.gr2",
            "meshes": [],
            "skeletons": [skeleton],
            "models": [{"name": "rig", "skeleton": skeleton, "meshBindings": []}],
            "animations": [
                {
                    "name": "face",
                    "duration": 1,
                    "trackGroups": [
                        {
                            "name": "Root",
                            "transformTracks": [],
                            "vectorTracks": [
                                {"name": "Smile", "dimension": 1, "valueCurve": curve1},
                                {"name": "Look", "dimension": 2, "valueCurve": curve2},
                            ],
                        }
                    ],
                }
            ],
        }
        result = project_cmf(source)
        self.assertEqual(result["meshes"], [])
        self.assertEqual(len(result["skeletons"]), 1)
        self.assertEqual(result["skeletons"][0]["parents"], [0xFFFFFFFF])
        self.assertEqual(
            [channel["targetType"] for channel in result["animations"][0]["channels"]],
            ["MorphTarget", "Other"],
        )
        second = result["animations"][0]["curves"][1]
        self.assertEqual(second["valueDimension"], 2)
        self.assertEqual(struct.unpack("<4f", bytes(second["values"])), (0, 1, 2, 3))


if __name__ == "__main__":
    unittest.main()

import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons" / "io_scene_carbon_gr2"))

from gr2.gsf import is_gsf_raw, project_gsf  # noqa: E402
from gr2.reader import RawGr2  # noqa: E402


class GsfTests(unittest.TestCase):
    def test_projects_state_machine_and_ordered_unique_references(self):
        root = {
            "ModelNameHint": "Character",
            "StateMachine": {"Name": "Main"},
            "AnimationSlots": [{"Name": "Idle"}],
            "AnimationSets": [
                {
                    "SourceFile": "idle.gr2",
                    "Nested": {"FilePath": "walk.gr2;clip", "Source": "idle.gr2"},
                }
            ],
        }
        raw = RawGr2(version=7, section_count=8, file_info=root)

        self.assertTrue(is_gsf_raw(raw))
        result = project_gsf(raw)
        self.assertEqual(result["format"], "gsf")
        self.assertEqual(result["container"]["sectionCount"], 8)
        self.assertEqual(
            result["animationSets"][0]["sourceFileReferences"],
            ["idle.gr2", "walk.gr2;clip"],
        )

    def test_rejects_plain_gr2_roots(self):
        raw = RawGr2(version=7, section_count=1, file_info={"Meshes": []})
        self.assertFalse(is_gsf_raw(raw))
        with self.assertRaisesRegex(ValueError, "Granny State"):
            project_gsf(raw)


if __name__ == "__main__":
    unittest.main()

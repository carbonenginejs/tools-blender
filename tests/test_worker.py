"""Decoding in another process, because a thread was not enough.

Measured, not assumed. With eight decode THREADS running, Blender's main
thread got 9.6% of the ticks it gets when idle and stalled up to 330ms -- the
GIL, which pure-Python work holds. With eight child PROCESSES: 68.2% and 8.3ms,
and the decodes run genuinely in parallel (24 in 39.6s against about 154s one
after another).
"""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))

from carbon_eve_resources.dds import worker  # noqa: E402

try:
    import bpy
except ImportError:                     # pragma: no cover - outside Blender
    bpy = None


class ChildScriptTests(unittest.TestCase):
    def test_the_child_never_imports_blender(self):
        # It cannot: bpy outside Blender is either missing or a different
        # thing entirely, and importing it would be the one mistake that
        # turns a fast decode back into a broken one.
        self.assertNotIn("bpy", worker.SCRIPT)

    def test_the_child_is_told_where_the_add_on_lives(self):
        # A child interpreter starts with Blender's own path, not ours.
        self.assertIn("sys.path.insert", worker.SCRIPT)
        self.assertTrue((worker.addons_directory()
                         / "carbon_eve_resources").is_dir())

    def test_a_texture_that_is_not_bc7_is_not_a_failure(self):
        # Exit 2 says "nothing to do", and must not be reported as an error:
        # most of a ship's textures are DXT5, which Blender reads itself.
        self.assertIn("sys.exit(0 if made else 2)", worker.SCRIPT)


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.previous = worker.python_executable

    def tearDown(self):
        worker.python_executable = self.previous

    def test_no_interpreter_means_fall_back_rather_than_fail(self):
        # A texture that arrives late beats one that never arrives.
        worker.python_executable = lambda: None
        self.assertFalse(worker.decode("in.dds", "out.png"))

    def test_a_missing_source_does_not_raise(self):
        self.assertFalse(worker.decode("nowhere.dds", "out.png", timeout=30))


@unittest.skipIf(bpy is None, "needs Blender")
class InterpreterTests(unittest.TestCase):
    def test_blender_ships_one_and_we_find_it(self):
        # sys.executable inside Blender is BLENDER: running that would start a
        # second Blender per texture.
        found = worker.python_executable()
        self.assertIsNotNone(found)
        self.assertTrue(found.is_file())
        self.assertNotEqual(found.name.lower(), "blender.exe")


if __name__ == "__main__":
    unittest.main()

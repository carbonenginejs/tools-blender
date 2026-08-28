"""Parsing geometry off the main thread.

Measured on a Legion: the main-thread build goes from 4.9s to 1.4s, with the
parse moved into child processes. On the biggest hull files in the cache the
parse alone is 17 to 19 seconds against 1.1 to 1.6 seconds of Blender work --
92 to 94 per cent of a geometry import is pure Python holding the main thread.
"""

from pathlib import Path
import pickle
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))

from carbon_eve_resources.gr2_importer import parse_cache  # noqa: E402


class ChildScriptTests(unittest.TestCase):
    def test_it_imports_the_parser_and_not_the_add_on(self):
        # `gr2_importer.addon` is Blender's side and imports bpy at the top.
        # A child that reached for read_gr2 there died on the import, fell
        # back to parsing in-process, and looked exactly like the feature
        # never existing.
        self.assertIn("gr2_importer.gr2 import read_gr2", parse_cache.SCRIPT)
        self.assertNotIn("gr2_importer.addon", parse_cache.SCRIPT)
        self.assertNotIn("bpy", parse_cache.SCRIPT)

    def test_the_child_writes_through_a_temporary_name(self):
        # A half-written parse is worse than none: it exists, so it is trusted.
        self.assertIn('.part', parse_cache.SCRIPT)
        self.assertIn("os.replace", parse_cache.SCRIPT)


class CachePathTests(unittest.TestCase):
    def test_it_sits_beside_its_source_with_an_extension(self):
        # The rule everything else follows: sources keep EVE's name, and only
        # a file we translated carries an extension.
        found = parse_cache.cache_path("/cache/ResFiles/b4/b40590_abc")
        self.assertEqual(found.parent.name, "b4")
        self.assertEqual(found.name, "b40590_abc" + parse_cache.SUFFIX)


class ReadingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="carbon-parsed-"))
        self.source = self.root / "b40590_abc"
        self.source.write_bytes(b"gr2")

    def write(self, envelope):
        parse_cache.cache_path(self.source).write_bytes(pickle.dumps(envelope))

    def test_a_current_file_comes_back(self):
        self.write({"version": parse_cache.VERSION, "parsed": {"meshes": []}})
        self.assertEqual(parse_cache.read(self.source), {"meshes": []})

    def test_an_older_version_is_ignored_rather_than_handed_over(self):
        # The parser's output changing shape must not feed a newer importer
        # something it does not understand.
        self.write({"version": parse_cache.VERSION - 1, "parsed": {"old": True}})
        self.assertIsNone(parse_cache.read(self.source))

    def test_a_corrupt_file_is_not_an_error(self):
        # The source is still there, and parsing it again always works.
        parse_cache.cache_path(self.source).write_bytes(b"not a pickle at all")
        self.assertIsNone(parse_cache.read(self.source))

    def test_an_empty_file_is_ignored(self):
        parse_cache.cache_path(self.source).write_bytes(b"")
        self.assertIsNone(parse_cache.read(self.source))

    def test_nothing_there_is_not_an_error(self):
        self.assertIsNone(parse_cache.read(self.source))


if __name__ == "__main__":
    unittest.main()

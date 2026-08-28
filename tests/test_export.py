"""Handing the files over.

The decisions worth pinning are about DUPLICATION: which files get copied,
where they land, and what happens on a second export into the same folder.
"""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))

from carbon_eve_resources.core import resfile  # noqa: E402


PATH = "res:/dx9/model/ship/amarr/ab1/ab1_t1_a.dds"


class DestinationTests(unittest.TestCase):
    def test_it_lands_in_the_layout_the_local_folder_reads(self):
        # So an export folder IS an authored folder: export, edit a file, and
        # the next load picks up the edit.
        found = resfile.export_destination("/out", PATH, "/cache/ResFiles/b4/abc")
        self.assertEqual(found,
                         Path("/out/dx9/model/ship/amarr/ab1/ab1_t1_a.dds"))

    def test_a_translated_texture_keeps_the_extension_it_can_be_opened_with(self):
        # BC7 goes out as the PNG we made of it. Handing somebody a .dds they
        # cannot open, under a name that says .dds, is worse than useless.
        found = resfile.export_destination("/out", PATH,
                                           "/cache/ResFiles/b4/abc.png")
        self.assertEqual(found.suffix, ".png")
        self.assertEqual(found.stem, "ab1_t1_a")

    def test_a_source_with_no_extension_keeps_the_logical_one(self):
        # The cache stores sources with no extension at all.
        found = resfile.export_destination("/out", PATH, "/cache/ResFiles/b4/abc")
        self.assertEqual(found.suffix, ".dds")

    def test_nothing_to_export_it_under(self):
        self.assertIsNone(resfile.export_destination("/out", "", "/cache/x"))


class WhoseFileIsItTests(unittest.TestCase):
    """`is_cached` decides by LOCATION, never by the shape of a name."""

    def setUp(self):
        from carbon_eve_resources import export

        self.export = export
        self.cache = Path(tempfile.mkdtemp(prefix="carbon-x-cache-"))
        self.theirs = Path(tempfile.mkdtemp(prefix="carbon-x-theirs-"))

    def image_at(self, path):
        class Image:
            filepath = str(path)
        return Image()

    def test_a_file_in_the_cache_is_ours(self):
        found = self.cache / "ResFiles" / "b4" / "b40590f110b66d26_abc"
        self.assertTrue(self.export.is_cached(self.image_at(found), self.cache))

    def test_a_file_in_their_folder_is_not(self):
        # Even one named like an address. Somebody's copy of ResFiles is full
        # of those, and every one of them is still theirs.
        found = self.theirs / "ResFiles" / "b4" / "b40590f110b66d26_abc"
        self.assertFalse(self.export.is_cached(self.image_at(found), self.cache))

    def test_a_readable_name_inside_the_cache_is_still_ours(self):
        found = self.cache / "dx9" / "model" / "ab1_t1_a.dds"
        self.assertTrue(self.export.is_cached(self.image_at(found), self.cache))

    def test_no_cache_configured_claims_nothing(self):
        self.assertFalse(self.export.is_cached(self.image_at("/x/y"), ""))


if __name__ == "__main__":
    unittest.main()

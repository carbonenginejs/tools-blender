"""Where a resource's bytes are kept.

We used to work the address out ourselves, from the logical path, and got it
wrong: the hash is over the LOWERCASED path, so the three paths with a capital
in them addressed a file that was not there and downloaded again on EVERY load.
The index already carries the address, so now it is used as given and never
recomputed -- which is why there is nothing here that could disagree with it.
"""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))

from carbon_eve_resources.core import resfile  # noqa: E402


#: A real row: the logical path a document spells, and the location its index
#: gives. Note the capitals -- this is one of the three that re-downloaded.
PATH = "res:/Texture/Global/noise32cube_volume.dds"
LOCATION = "b4/b40590f110b66d26_f26d631c8e5491e4c1f3273b29019fce"


class StoredPathTests(unittest.TestCase):
    def test_the_location_is_used_exactly_as_given(self):
        found = resfile.stored_path("/cache", LOCATION, PATH)
        self.assertEqual(found.parent.name, "b4")
        self.assertTrue(found.name.startswith("b40590f110b66d26_"))

    def test_a_source_keeps_no_extension(self):
        # EVE's layout, byte for byte, so a file is found whoever put it
        # there. Blender reads an extensionless DDS fine -- verified against a
        # real one, 2048x1024 and four channels -- so renaming what we were
        # given buys nothing.
        self.assertEqual(resfile.stored_path("/cache", LOCATION, PATH).suffix,
                         "")

    def test_the_logical_path_cannot_add_one(self):
        self.assertEqual(
            resfile.stored_path("/cache", LOCATION, "res:/a/b.dds").suffix, "")

    def test_casing_in_the_path_cannot_change_where_the_file_goes(self):
        # The old bug, made impossible: the address comes from the row, so how
        # a document spells the path is no longer part of the answer.
        lower = resfile.stored_path("/cache", LOCATION, PATH.lower())
        self.assertEqual(resfile.stored_path("/cache", LOCATION, PATH), lower)

    def test_a_plain_path_is_not_an_address(self):
        # An index carries overlay rows that are not content-addressed.
        self.assertIsNone(resfile.stored_path("/cache", "not/an/address", PATH))
        self.assertIsNone(resfile.parse("not/an/address"))


class ReadablePathTests(unittest.TestCase):
    def test_it_drops_the_scheme(self):
        found = resfile.readable_path("/cache", "res:/dx9/model/ship/a.gr2")
        self.assertEqual(found, Path("/cache/dx9/model/ship/a.gr2"))

    def test_an_empty_path_has_nowhere_to_go(self):
        self.assertIsNone(resfile.readable_path("/cache", ""))


class DerivedBesideSourceTests(unittest.TestCase):
    """A decoded texture goes beside its source, same name, new extension."""

    def test_a_decode_inherits_the_source_address(self):
        from carbon_eve_resources.dds.reader import derived_path

        source = resfile.stored_path("/cache", LOCATION, PATH)
        decoded = derived_path(source)
        self.assertEqual(decoded.parent, source.parent)
        self.assertEqual(decoded.stem, source.stem)
        self.assertEqual(decoded.suffix, ".png")

    def test_pruning_keeps_a_decode_with_its_source(self):
        # Content-addressed, so the decode is shared by every hull using that
        # texture, and it is dropped with the build it belongs to.
        from carbon_eve_resources.core import cache_prune

        root = Path(tempfile.mkdtemp(prefix="carbon-derived-"))
        (root / "indexes").mkdir(parents=True)
        (root / "indexes" / "resfileindex-300.txt").write_text(
            f"{PATH},{LOCATION},x,1,1\n", encoding="utf-8")
        source = resfile.stored_path(root, LOCATION, PATH)
        source.parent.mkdir(parents=True)
        source.write_bytes(b"dds")
        decoded = source.with_suffix(".png")
        decoded.write_bytes(b"png")
        stale = source.parent / ("0" * 16 + "_" + "0" * 32 + ".dds")
        stale.write_bytes(b"old")

        cache_prune.prune(root, apply=True)

        self.assertTrue(source.exists())
        self.assertTrue(decoded.exists())
        self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main()

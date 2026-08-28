"""An optional folder of hand-authored files wins over the cache and CCP."""

from pathlib import Path
import sys
import tempfile
import unittest


ADDONS = Path(__file__).resolve().parents[1] / "addons"
if str(ADDONS) not in sys.path:
    sys.path.insert(0, str(ADDONS))

from carbon_eve_resources.core.sof_fetch import local_file  # noqa: E402


TEXTURE = "res:/dx9/model/ship/gallente/battleship/gb2/gb2_t1_a.dds"
GEOMETRY = "res:/dx9/model/ship/gallente/battleship/gb2/gb2_t1.gr2"


class LocalSourceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="carbon-local-"))
        self.folder = self.root / "dx9/model/ship/gallente/battleship/gb2"
        self.folder.mkdir(parents=True)

    def _write(self, name, data=b"x"):
        path = self.folder / name
        path.write_bytes(data)
        return path

    def test_no_root_means_no_local_file(self):
        self.assertIsNone(local_file(None, TEXTURE))
        self.assertIsNone(local_file("", TEXTURE))

    def test_nothing_there_falls_through(self):
        self.assertIsNone(local_file(self.root, TEXTURE))

    def test_a_tga_is_taken_for_a_dds_path(self):
        # The logical path says .dds; a hand-authored source is usually a TGA.
        wanted = self._write("gb2_t1_a.tga")
        self.assertEqual(local_file(self.root, TEXTURE), wanted)

    def test_a_dds_is_taken_when_there_is_no_tga(self):
        wanted = self._write("gb2_t1_a.dds")
        self.assertEqual(local_file(self.root, TEXTURE), wanted)

    def test_the_tga_wins_over_the_dds(self):
        self._write("gb2_t1_a.dds")
        wanted = self._write("gb2_t1_a.tga")
        self.assertEqual(local_file(self.root, TEXTURE), wanted)

    def test_a_png_is_taken_when_there_is_no_tga(self):
        self._write("gb2_t1_a.dds")
        wanted = self._write("gb2_t1_a.png")
        self.assertEqual(local_file(self.root, TEXTURE), wanted)

    def test_the_order_is_tga_then_png_then_dds(self):
        self._write("gb2_t1_a.dds")
        self._write("gb2_t1_a.png")
        wanted = self._write("gb2_t1_a.tga")
        self.assertEqual(local_file(self.root, TEXTURE), wanted)

    def test_geometry_is_taken_under_its_own_name(self):
        wanted = self._write("gb2_t1.gr2")
        self.assertEqual(local_file(self.root, GEOMETRY), wanted)

    def test_geometry_does_not_substitute_extensions(self):
        # A .tga is not geometry; only textures have alternates.
        self._write("gb2_t1.tga")
        self.assertIsNone(local_file(self.root, GEOMETRY))

    def test_an_empty_file_is_not_a_file(self):
        # A zero-byte placeholder would otherwise mask the real resource.
        self._write("gb2_t1_a.tga", b"")
        self.assertIsNone(local_file(self.root, TEXTURE))

    def test_the_layout_is_the_logical_path(self):
        # Mirrors the resfileindex's logical paths, so no content addressing
        # has to be understood to drop a file in.
        wanted = self._write("gb2_t1_a.tga")
        self.assertEqual(
            wanted.relative_to(self.root).as_posix(),
            TEXTURE.split(":/", 1)[1].rsplit(".", 1)[0] + ".tga")


if __name__ == "__main__":
    unittest.main()


class PrecedenceTests(unittest.TestCase):
    """Optional folder, then the cache read the same way, then the index."""

    def setUp(self):
        self.optional = Path(tempfile.mkdtemp(prefix="carbon-opt-"))
        self.cache = Path(tempfile.mkdtemp(prefix="carbon-cache-"))
        self.relative = "dx9/model/ship/gallente/battleship/gb2"
        for root in (self.optional, self.cache):
            (root / self.relative).mkdir(parents=True)

    def _write(self, root, name):
        path = root / self.relative / name
        path.write_bytes(b"x")
        return path

    def test_the_optional_folder_beats_the_cache(self):
        self._write(self.cache, "gb2_t1_a.tga")
        wanted = self._write(self.optional, "gb2_t1_a.tga")
        self.assertEqual(local_file(self.optional, TEXTURE), wanted)

    def test_the_cache_is_read_the_same_way(self):
        # tga first there too, not only in the optional folder.
        self._write(self.cache, "gb2_t1_a.dds")
        wanted = self._write(self.cache, "gb2_t1_a.tga")
        self.assertEqual(local_file(self.cache, TEXTURE), wanted)

    def test_neither_having_it_falls_through(self):
        self.assertIsNone(local_file(self.optional, TEXTURE))
        self.assertIsNone(local_file(self.cache, TEXTURE))

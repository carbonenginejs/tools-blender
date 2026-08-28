"""Writing a PNG without Blender, so the decode can leave the main thread.

`Image.save` is Blender's, and Blender's data is main-thread only -- which is
the one thread a nine-second decode must not be on. Everything here is plain
zlib and struct so it can run in the fetch pool beside the downloads.
"""

from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))

from carbon_eve_resources.dds import png  # noqa: E402

try:
    import bpy
except ImportError:                     # pragma: no cover - outside Blender
    bpy = None


def chunks(data: bytes):
    """Every chunk in a PNG, as `(type, payload)`."""

    found, at = [], len(png.SIGNATURE)
    while at < len(data):
        length = struct.unpack(">I", data[at:at + 4])[0]
        kind = data[at + 4:at + 8]
        found.append((kind, data[at + 8:at + 8 + length]))
        at += 12 + length
    return found


class EncodingTests(unittest.TestCase):
    def test_it_starts_with_the_png_signature(self):
        made = png.encode(1, 1, bytes([255, 0, 0, 255]))
        self.assertTrue(made.startswith(png.SIGNATURE))

    def test_the_header_says_8_bit_rgba(self):
        made = png.encode(4, 2, bytes(4 * 2 * 4))
        kind, payload = chunks(made)[0]
        self.assertEqual(kind, b"IHDR")
        width, height, depth, colour = struct.unpack(">IIBB", payload[:10])
        self.assertEqual((width, height, depth, colour), (4, 2, 8, 6))

    def test_every_chunk_carries_a_correct_crc(self):
        # A wrong CRC is a file some readers accept and others reject, which is
        # the worst kind of broken.
        made = png.encode(3, 3, bytes(3 * 3 * 4))
        at = len(png.SIGNATURE)
        while at < len(made):
            length = struct.unpack(">I", made[at:at + 4])[0]
            body = made[at + 4:at + 8 + length]
            stated = struct.unpack(">I", made[at + 8 + length:at + 12 + length])[0]
            self.assertEqual(zlib.crc32(body) & 0xFFFFFFFF, stated)
            at += 12 + length

    def test_the_rows_come_back_in_the_order_they_went_in(self):
        # Top row first. Blender's own pixel buffer is the other way up, and
        # getting this backwards gives an upside-down hull that reads as a UV
        # bug rather than a writer bug.
        top = bytes([255, 0, 0, 255])
        bottom = bytes([0, 0, 255, 255])
        made = png.encode(1, 2, top + bottom)
        raw = zlib.decompress(next(p for k, p in chunks(made) if k == b"IDAT"))
        self.assertEqual(raw[0], 0)             # filter byte
        self.assertEqual(raw[1:5], top)
        self.assertEqual(raw[6:10], bottom)

    def test_it_ends_with_iend(self):
        self.assertEqual(chunks(png.encode(1, 1, bytes(4)))[-1][0], b"IEND")


class WritingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="carbon-png-"))

    def test_no_half_written_file_is_left_behind(self):
        # A half-written PNG is worse than none: it exists, so the decode is
        # never run again, and every load afterwards shows a broken image.
        target = self.root / "deep" / "one.png"
        png.write(target, 2, 2, bytes(2 * 2 * 4))
        self.assertTrue(target.is_file())
        self.assertEqual(list(self.root.rglob("*.part")), [])


@unittest.skipIf(bpy is None, "needs Blender")
class BlenderReadsItTests(unittest.TestCase):
    """The only test that matters in the end: Blender opens what we wrote."""

    def test_a_written_png_loads_at_the_right_size(self):
        root = Path(tempfile.mkdtemp(prefix="carbon-png-read-"))
        target = png.write(root / "checker.png", 4, 3,
                           bytes([200, 100, 50, 255] * 12))
        image = bpy.data.images.load(str(target))
        try:
            self.assertEqual(tuple(image.size), (4, 3))
            self.assertGreater(len(image.pixels), 0)
        finally:
            bpy.data.images.remove(image)


if __name__ == "__main__":
    unittest.main()

"""Authored volume slices and mip levels are not interchangeable with 2D mips."""
from pathlib import Path
import struct
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))
from carbon_eve_resources.dds.reader import DdsError
from carbon_eve_resources.dds.volume import decode_volume


def fixture(alpha=False):
    header = bytearray(128)
    header[:4] = b"DDS "
    struct.pack_into("<IIIIIII", header, 4, 124, 0x80000f, 2, 2, 8 if alpha else 6, 2, 2)
    struct.pack_into("<IIIIIIII", header, 76, 32, 0x41 if alpha else 0x40, 0,
                     32 if alpha else 24, 0xff0000, 0xff00, 0xff, 0xff000000 if alpha else 0)
    struct.pack_into("<I", header, 112, 0x200000)
    texels = [(i, i+10, i+20, i+30) for i in range(9)]
    return bytes(header) + bytes(v for pixel in texels for v in (pixel if alpha else pixel[:3]))


class VolumeTests(unittest.TestCase):
    def test_rgb_slices_and_authored_depth_reduction(self):
        levels = decode_volume(fixture())
        self.assertEqual([(l.width,l.height,l.depth) for l in levels], [(2,2,2),(1,1,1)])
        self.assertEqual(levels[0].rgba[:4], bytes((20,10,0,255)))
        self.assertEqual(levels[0].rgba[16:20], bytes((24,14,4,255)))
        self.assertEqual(levels[1].rgba, bytes((28,18,8,255)))

    def test_alpha_is_preserved_without_rgb_color_conversion(self):
        levels = decode_volume(fixture(True))
        self.assertEqual(levels[0].rgba[:4], bytes((20,10,0,30)))
        self.assertEqual(levels[1].rgba, bytes((28,18,8,38)))

    def test_truncated_mip_and_invalid_format_fail_explicitly(self):
        with self.assertRaisesRegex(DdsError, "mip 1 is truncated"):
            decode_volume(fixture()[:-1])
        for offset, value in ((80, 4), (28, 8), (92, 0xff00)):
            data = bytearray(fixture())
            struct.pack_into("<I", data, offset, value)
            with self.subTest(offset=offset), self.assertRaises(DdsError):
                decode_volume(data)


if __name__ == "__main__":
    unittest.main()

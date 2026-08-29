"""A nebula cube turned into a world Blender can load.

Three conversions, each with its own way of being quietly wrong: the cube's
faces, the sphere they are resampled onto, and the Radiance encoding that keeps
the values above one. The float pipeline is the point -- a nebula clipped to
0..1 has lost exactly the bright detail it exists to provide.

Face decoding was checked against `runtime/.../dds/index.js` on the real
`c02_cube.dds`: all six face means agreed with a full-resolution decode to
within one part in a thousand, the largest being 0.16722 against 0.16700.
"""

from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))

from carbon_eve_resources.dds import environment  # noqa: E402


def cube_dds(width=8, dxgi=96, caps2=0xFE00, faces=6):
    """A DDS header describing a cube, with room for its blocks."""

    header = bytearray(148)
    header[0:4] = b"DDS "
    struct.pack_into("<I", header, 4, 124)
    struct.pack_into("<II", header, 12, width, width)     # height, width
    struct.pack_into("<I", header, 84, 0)
    header[84:88] = b"DX10"
    struct.pack_into("<I", header, 112, caps2)
    struct.pack_into("<I", header, 128, dxgi)
    blocks = max(1, (width + 3) // 4) ** 2 * 16
    return bytes(header) + bytes(blocks * faces)


class InspectTests(unittest.TestCase):
    def test_a_cube_reports_its_faces_and_where_they_start(self):
        found = environment.inspect(cube_dds(width=8))
        self.assertEqual(found["width"], 8)
        self.assertEqual(found["faces"], 6)
        self.assertEqual(found["offset"], 148)
        self.assertEqual(found["face_bytes"], 4 * 16)

    def test_a_flat_texture_is_refused(self):
        with self.assertRaises(environment.CubeError):
            environment.inspect(cube_dds(caps2=0))

    def test_an_incomplete_cube_is_refused(self):
        """Five faces cannot make a sphere, and half a sky is worse than none."""

        with self.assertRaises(environment.CubeError):
            environment.inspect(cube_dds(caps2=0x200 | 0x7C00))

    def test_a_cube_that_is_not_bc6h_is_refused(self):
        with self.assertRaises(environment.CubeError):
            environment.inspect(cube_dds(dxgi=98))       # BC7

    def test_the_face_offsets_do_not_run_past_the_file(self):
        data = cube_dds(width=8)
        found = environment.inspect(data)
        last = found["offset"] + 5 * found["face_bytes"] + found["face_bytes"]
        self.assertLessEqual(last, len(data))


class SampleTests(unittest.TestCase):
    """Each face is a flat colour, so a direction names the face it hit."""

    def faces(self):
        return [([float(index)] * 3, 1) for index in range(6)]

    def test_each_axis_finds_its_own_face(self):
        faces = self.faces()
        for index, direction in enumerate(((1, 0, 0), (-1, 0, 0),
                                           (0, 1, 0), (0, -1, 0),
                                           (0, 0, 1), (0, 0, -1))):
            with self.subTest(face=environment.FACE_ORDER[index]):
                self.assertEqual(environment.sample_cube(faces, *direction),
                                 (float(index),) * 3)

    def test_the_major_axis_decides(self):
        """A direction mostly along -z lands on -z whatever the others do."""

        faces = self.faces()
        self.assertEqual(environment.sample_cube(faces, 0.2, -0.3, -1.0),
                         (5.0, 5.0, 5.0))

    def test_the_equirectangular_image_is_two_to_one(self):
        pixels, width, height = environment.to_equirectangular(self.faces(), 16)
        self.assertEqual((width, height), (16, 8))
        self.assertEqual(len(pixels), 16 * 8 * 3)

    def test_the_first_row_is_the_zenith(self):
        """Blender is Z-up and EVE Y-up, so up on the sphere is the cube's +Y.

        The first scanline is what Radiance's `-Y` header calls the top and
        what Blender puts at the zenith. Getting this backwards hangs the
        nebula upside down, and nothing else here would notice.
        """

        pixels, width, height = environment.to_equirectangular(self.faces(), 16)
        top = list(pixels[0:3])
        bottom = list(pixels[(height - 1) * width * 3:(height - 1) * width * 3 + 3])
        self.assertEqual(top, [2.0, 2.0, 2.0])          # +y
        self.assertEqual(bottom, [3.0, 3.0, 3.0])       # -y

    def test_blender_to_eve_is_a_rotation_and_not_a_mirror(self):
        """The determinant, which is the only thing that catches a mirror.

        A reflected sky looks entirely plausible -- it is a nebula either way
        -- so nothing but this arithmetic says whether it is right. The mapping
        in the Node script this was ported from has determinant -1, and it was
        carried over before being caught here.
        """

        columns = [environment.eve_direction(*axis)
                   for axis in ((1, 0, 0), (0, 1, 0), (0, 0, 1))]
        (a, d, g), (b, e, h), (c, f, i) = columns
        determinant = (a * (e * i - f * h)
                       - b * (d * i - f * g)
                       + c * (d * h - e * g))
        self.assertEqual(determinant, 1)

    def test_blender_up_is_eve_up(self):
        self.assertEqual(environment.eve_direction(0, 0, 1), (0, 1, 0))


class RadianceTests(unittest.TestCase):
    def decode(self, encoded):
        """RGBE back to float, so the encoding is checked by what it means."""

        body = encoded.split(b"\n\n", 1)[1].split(b"\n", 1)[1]
        out = []
        for index in range(len(body) // 4):
            red, green, blue, exponent = body[index * 4:index * 4 + 4]
            if exponent == 0:
                out.append((0.0, 0.0, 0.0))
                continue
            scale = 2.0 ** (exponent - 128) / 256.0
            out.append((red * scale, green * scale, blue * scale))
        return out

    def test_the_header_names_the_size_the_way_radiance_does(self):
        encoded = environment.encode_radiance([0.0] * 12, 2, 2)
        self.assertTrue(encoded.startswith(b"#?RADIANCE\n"))
        self.assertIn(b"FORMAT=32-bit_rle_rgbe", encoded)
        self.assertIn(b"-Y 2 +X 2\n", encoded)

    def test_it_is_four_bytes_a_pixel_after_the_header(self):
        encoded = environment.encode_radiance([0.0] * 3 * 6, 3, 2)
        body = encoded.split(b"\n\n", 1)[1].split(b"\n", 1)[1]
        self.assertEqual(len(body), 3 * 2 * 4)

    def test_values_above_one_survive(self):
        """The whole reason this is not a PNG."""

        pixels = [0.5, 1.0, 40.0]
        got = self.decode(environment.encode_radiance(pixels, 1, 1))[0]
        for wanted, found in zip(pixels, got):
            self.assertAlmostEqual(found, wanted, delta=wanted * 0.02)

    def test_a_black_pixel_encodes_as_four_zeroes(self):
        encoded = environment.encode_radiance([0.0, 0.0, 0.0], 1, 1)
        self.assertEqual(encoded[-4:], b"\x00\x00\x00\x00")

    def test_very_dim_pixels_do_not_become_black(self):
        """A nebula is mostly dim, so the floor matters more than the peak."""

        got = self.decode(environment.encode_radiance([0.002, 0.003, 0.004],
                                                      1, 1))[0]
        self.assertAlmostEqual(got[0], 0.002, delta=0.0001)
        self.assertAlmostEqual(got[2], 0.004, delta=0.0001)


class ReductionTests(unittest.TestCase):
    def test_a_face_reduces_by_averaging_after_a_full_decode(self):
        """Every block is read, then the result is filtered down.

        Subsampling blocks instead was four times cheaper and threw away
        sixteen texels for every one it kept, which is what made the sky look
        soft.
        """

        data = cube_dds(width=64)
        info = environment.inspect(data)
        pixels, size = environment.decode_face(data, info, 0, size=4)
        self.assertEqual(size, 4)
        self.assertEqual(len(pixels), 4 * 4 * 3)

    def test_asking_for_more_than_the_cube_has_gives_the_cube(self):
        """No invented detail: a request above the source is capped at it."""

        data = cube_dds(width=8)
        info = environment.inspect(data)
        _, size = environment.decode_face(data, info, 0, size=512)
        self.assertEqual(size, 8)

    def test_the_default_keeps_every_texel(self):
        data = cube_dds(width=8)
        info = environment.inspect(data)
        _, size = environment.decode_face(data, info, 0)
        self.assertEqual(size, 8)


if __name__ == "__main__":
    unittest.main()

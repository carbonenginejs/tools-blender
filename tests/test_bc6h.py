"""BC6H, the HDR block format the nebulae are stored in.

The vectors below are not hand-computed. They are real blocks from
`res:/dx9/scene/universe/c02_cube.dds` decoded by
`runtime/src/resource/formats/dds/core/bc6h.js`, which is the CarbonEngineJS
decoder and the authority this is a port of. A test that restated the port's
own arithmetic would agree with any bug in it; these agree with the runtime or
they fail.

The full check was wider than this: 4000 blocks through both decoders, both
signs, 18 distinct mode codes, zero differences to the last bit. Flipping the
sign flag on one side moved 168456 of 256000 values, so the agreement is a
measurement and not a comparison that cannot fail.
"""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))

from carbon_eve_resources.dds import bc6h  # noqa: E402


#: `(mode, block, first texel RGB, last texel RGB)`, signed -- these come from
#: a DXGI 96 cube, and 96 is SF16.
VECTORS = (
    (11, "6b63a3d576271e7b305555789aaaeedc",
     (0.130127, 0.211426, 0.309082), (0.112915, 0.187134, 0.276855)),
    (11, "0b63a1d19ee79e7c240063358878cf8a",
     (0.122986, 0.201538, 0.297852), (0.118225, 0.194946, 0.287598)),
    (2, "c228acda2a8a90918ae1404afcf9f0c0",
     (0.029434, 0.044647, 0.066528), (0.031555, 0.048187, 0.072510)),
    (2, "4237e6a6fbe07900004f95c449bca45c",
     (0.347900, 0.484131, 0.576172), (0.346680, 0.484131, 0.576172)),
    (11, "8b63acf5f6471f7e9ee9439931440010",
     (0.130249, 0.240234, 0.362549), (0.131714, 0.244751, 0.368652)),
    (11, "6b3c0b79d4871f0000f500f9f0fff0ff",
     (0.005150, 0.008514, 0.013008), (0.004795, 0.008041, 0.013008)),
    (0, "700b30c80001430c0010000000000000",
     (0.001504, 0.001800, 0.002119), (0.001504, 0.001800, 0.002119)),
    (11, "8bb3ddba03a01e0000000000000000e0",
     (0.002428, 0.003345, 0.004795), (0.002428, 0.003040, 0.004795)),
    (2, "22218a48e2fc75a07930c020089e3b3f",
     (0.008102, 0.010704, 0.014488), (0.007484, 0.010231, 0.013779)),
    (2, "629a71ea290000a001e00200078e0300",
     (0.002739, 0.003685, 0.005592), (0.002739, 0.003685, 0.005238)),
)


class RuntimeAgreementTests(unittest.TestCase):
    def test_real_blocks_decode_as_the_runtime_decodes_them(self):
        for mode, block, first, last in VECTORS:
            with self.subTest(block=block):
                pixels = bc6h.decode_block(bytes.fromhex(block), signed=True)
                for channel in range(3):
                    self.assertAlmostEqual(pixels[channel], first[channel],
                                           places=5)
                    self.assertAlmostEqual(pixels[60 + channel], last[channel],
                                           places=5)

    def test_every_block_is_opaque(self):
        for _, block, _, _ in VECTORS:
            pixels = bc6h.decode_block(bytes.fromhex(block), signed=True)
            self.assertEqual([pixels[texel * 4 + 3] for texel in range(16)],
                             [1.0] * 16)

    def test_reading_a_block_as_the_wrong_sign_changes_it(self):
        """The negative control: the vectors above could have failed."""

        block = bytes.fromhex(VECTORS[0][1])
        self.assertNotEqual(bc6h.decode_block(block, signed=True)[:3],
                            bc6h.decode_block(block, signed=False)[:3])


class FormatTests(unittest.TestCase):
    def test_the_dxgi_numbers_are_the_ones_direct3d_uses(self):
        """94/95/96, not 95/96/97.

        Worth pinning: off by one here decodes an unsigned cube as signed and
        the nebula comes out about a hundred times too dark, with no error
        anywhere. That is exactly what happened.
        """

        self.assertEqual(bc6h.DXGI_BC6H_TYPELESS, 94)
        self.assertEqual(bc6h.DXGI_BC6H_UF16, 95)
        self.assertEqual(bc6h.DXGI_BC6H_SF16, 96)

    def test_is_bc6h_accepts_only_those_three(self):
        for value in (94, 95, 96):
            self.assertTrue(bc6h.is_bc6h(value))
        for value in (97, 98, 99, 0, 71):
            self.assertFalse(bc6h.is_bc6h(value))


class TableTests(unittest.TestCase):
    def test_there_is_one_layout_per_mode(self):
        self.assertEqual(len(bc6h.MODE_LAYOUTS), len(bc6h.MODES))
        self.assertEqual(len(bc6h.DESCRIPTORS), len(bc6h.MODES))

    def test_a_layout_covers_every_header_bit(self):
        """82 for two subsets, 65 for one -- where the colour indices start."""

        for index, (mode, descriptor) in enumerate(
                zip(bc6h.MODES, bc6h.DESCRIPTORS)):
            header = 82 if mode[1] == 2 else 65
            with self.subTest(mode=index):
                self.assertGreaterEqual(len(descriptor), header)

    def test_a_reserved_mode_is_opaque_black_rather_than_an_error(self):
        """Codes 19, 23, 27 and 31 are reserved.

        A file using one is broken, but a broken block should not take a whole
        nebula down with it.
        """

        block = bytes([0x13]) + bytes(15)
        pixels = bc6h.decode_block(block)
        self.assertEqual(pixels[:4], [0.0, 0.0, 0.0, 1.0])


if __name__ == "__main__":
    unittest.main()

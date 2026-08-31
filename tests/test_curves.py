import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "carbon-cmf" / "src"))
sys.path.insert(0, str(ROOT / "packages" / "carbon-granny" / "src"))
sys.path.insert(0, str(ROOT / "packages" / "carbon-gr2" / "src"))

from carbon_gr2 import decode_curve, sample_curve  # noqa: E402
from carbon_gr2.tangents import unpack_mesh_tangents  # noqa: E402


T1 = 0x3F80


class CurveTests(unittest.TestCase):
    def test_keyframes_and_sampling(self):
        decoded = decode_curve(
            {"format": 0, "degree": 0, "dimension": 3, "controls": [1, 2, 3, 4, 5, 6]},
            3,
        )
        self.assertEqual(decoded["knots"], [0, 1])
        output = [0.0, 0.0, 0.0]
        sample_curve(output, decoded, 0.25, duration=1, keyframed=True)
        self.assertEqual(output, [1, 2, 3])
        sample_curve(output, decoded, 0.75, duration=1, keyframed=True)
        self.assertEqual(output, [4, 5, 6])

    def test_linear_sampling(self):
        curve = {
            "knots": [0, 10],
            "controls": [0, 0, 0, 10, 20, 30],
            "degree": 1,
            "dimension": 3,
        }
        output = [0.0, 0.0, 0.0]
        sample_curve(output, curve, 5, duration=10)
        self.assertEqual(output, [5, 10, 15])

    def test_quantized_arbitrary_dimension(self):
        scale_offsets = [0.5, 0.25, 2, 1, -1, 0, 1, 0.5]
        values = [10, 20, 2, 4, 1, 3, 6, 8, 5, 7]
        expected = [0, 1, 3, 3.5, 2, 2, 11, 7.5]
        for curve_format in (6, 7):
            with self.subTest(curve_format=curve_format):
                decoded = decode_curve(
                    {
                        "format": curve_format,
                        "degree": 1,
                        "oneOverKnotScaleTrunc": T1,
                        "controlScaleOffsets": scale_offsets,
                        "knotsControls": values,
                    },
                    4,
                )
                self.assertEqual(decoded["knots"], [10, 20])
                self.assertEqual(decoded["controls"], expected)

    def test_matrix_diagonal_formats(self):
        for curve_format in (12, 14):
            decoded = decode_curve(
                {
                    "format": curve_format,
                    "degree": 1,
                    "oneOverKnotScaleTrunc": T1,
                    "controlScales": [0.5],
                    "controlOffsets": [1],
                    "knotsControls": [3, 6, 2, 4],
                },
                9,
            )
            self.assertEqual(decoded["knots"], [3, 6])
            self.assertEqual(
                decoded["controls"],
                [2, 0, 0, 0, 2, 0, 0, 0, 2, 3, 0, 0, 0, 3, 0, 0, 0, 3],
            )

    def test_identity_and_invalid_dimension(self):
        self.assertEqual(decode_curve({"format": 2, "degree": 0}, 4)["controls"], [0, 0, 0, 1])
        self.assertEqual(decode_curve({"format": 2, "degree": 0}, 2)["controls"], [0, 0])
        with self.assertRaisesRegex(ValueError, "does not match track dimension"):
            decode_curve({"format": 4, "controls": [1, 2, 3]}, 4)

    def test_gr2_legacy_null_tangent_uses_shared_decoder(self):
        mesh = {
            "vertex": {
                "position": [0.0, 0.0, 0.0],
                "normal": [],
                "tangent": [0.0, 1.0, 0.0, 1.0],
                "binormal": [],
            }
        }
        self.assertTrue(unpack_mesh_tangents(mesh))
        self.assertEqual(mesh["vertex"]["normal"], [0.0, 0.0, 0.0])
        self.assertEqual(mesh["vertex"]["tangent"], [0.0, 0.0, 0.0])
        self.assertEqual(mesh["vertex"]["binormal"], [0.0, 0.0, 0.0])


if __name__ == "__main__":
    unittest.main()

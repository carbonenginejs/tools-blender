import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons" / "carbon_eve_resources" / "gr2_importer"))

from gr2 import decode_curve, sample_curve  # noqa: E402


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
        with self.assertRaisesRegex(ValueError, "does not match track dimension"):
            decode_curve({"format": 4, "controls": [1, 2, 3]}, 4)


if __name__ == "__main__":
    unittest.main()

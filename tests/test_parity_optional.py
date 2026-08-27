import json
import math
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons" / "carbon_eve_resources" / "gr2_importer"))

from gr2 import read_gr2  # noqa: E402


def first_difference(left, right, path="$"):
    if isinstance(left, float) and isinstance(right, (float, int)):
        if math.isclose(left, float(right), rel_tol=1e-6, abs_tol=1e-7):
            return None
    elif type(left) is not type(right):
        return f"{path}: {type(left).__name__} != {type(right).__name__}"
    elif isinstance(left, dict):
        if left.keys() != right.keys():
            return f"{path}: keys {left.keys() ^ right.keys()}"
        for key in left:
            difference = first_difference(left[key], right[key], f"{path}.{key}")
            if difference:
                return difference
        return None
    elif isinstance(left, list):
        if len(left) != len(right):
            return f"{path}: length {len(left)} != {len(right)}"
        for index, (left_value, right_value) in enumerate(zip(left, right)):
            difference = first_difference(left_value, right_value, f"{path}[{index}]")
            if difference:
                return difference
        return None
    elif left == right:
        return None
    return f"{path}: {left!r} != {right!r}"


class OptionalFormatGr2ParityTests(unittest.TestCase):
    def _paths(self):
        sample = os.environ.get("GR2_PARITY_SAMPLE")
        format_root = os.environ.get("FORMAT_GR2_ROOT")
        if not sample or not format_root:
            self.skipTest("set GR2_PARITY_SAMPLE and FORMAT_GR2_ROOT to run parity")
        return sample, format_root

    def test_json_projection_matches_format_gr2(self):
        sample, format_root = self._paths()
        script = Path(format_root) / "bin" / "gr2reader.js"
        expected = json.loads(
            subprocess.check_output(
                ["node", str(script), sample, "--stdout"],
                text=True,
            )
        )
        actual = read_gr2(sample, decompress_curves=False, unpack_tangents=False)
        difference = first_difference(actual, expected)
        self.assertIsNone(difference, difference)

    def test_curve_and_tangent_expansion_matches_format_gr2(self):
        sample, format_root = self._paths()
        source = r"""
            import { readFileSync } from 'node:fs';
            import { pathToFileURL } from 'node:url';
            const sample = process.argv[1];
            const root = process.argv[2];
            const module = await import(pathToFileURL(root + '/src/index.js').href);
            const CjsFormatGr2 = module.default || module.CjsFormatGr2;
            const result = CjsFormatGr2.read(readFileSync(sample), {
                decompressCurves: true,
                unpackTangents: true
            });
            process.stdout.write(JSON.stringify(CjsFormatGr2.toJSON(result)));
        """
        expected = json.loads(
            subprocess.check_output(
                ["node", "--input-type=module", "-e", source, sample, format_root],
                text=True,
            )
        )
        actual = read_gr2(sample, decompress_curves=True, unpack_tangents=True)
        difference = first_difference(actual, expected)
        self.assertIsNone(difference, difference)


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import sys
import unittest


ADDONS = Path(__file__).resolve().parents[1] / "addons"
if str(ADDONS) not in sys.path:
    sys.path.insert(0, str(ADDONS))

from carbon_eve_resources.core.sof_builder import (  # noqa: E402
    SofBuilderError,
    normalize_dna,
)


class NormalizeDnaTests(unittest.TestCase):
    """A DNA string is validated before anything is asked to fetch it."""

    def test_accepts_a_plain_dna(self):
        self.assertEqual(normalize_dna(" MDE3_T3:Legion_Minmatar:Minmatar "),
                         "MDE3_T3:Legion_Minmatar:Minmatar")

    def test_accepts_commands(self):
        dna = "mde3_t3:legion_minmatar:minmatar:pattern?a;b;c"
        self.assertEqual(normalize_dna(dna), dna)

    def test_rejects_an_empty_dna(self):
        with self.assertRaises(SofBuilderError):
            normalize_dna("")

    def test_rejects_too_few_parts(self):
        with self.assertRaises(SofBuilderError):
            normalize_dna("mde3_t3:legion_minmatar")

    def test_rejects_characters_that_could_reach_the_shell(self):
        # The service accepts SOF grammar, not arbitrary command text or paths.
        for bad in ("mde3_t3:a:b; rm -rf /", "../../etc/passwd:a:b", "a:b:c|whoami"):
            with self.assertRaises(SofBuilderError):
                normalize_dna(bad)


if __name__ == "__main__":
    unittest.main()

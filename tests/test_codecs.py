import struct
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons" / "carbon_eve_resources" / "gr2_importer"))

from gr2.codecs import BITKNIT2_MAGIC, decompress_bitknit2  # noqa: E402


def raw_bitknit2(*quanta: bytes) -> bytes:
    stream = bytearray(struct.pack("<H", BITKNIT2_MAGIC))
    for quantum in quanta:
        stream.extend(b"\0\0")
        stream.extend(quantum)
        if len(quantum) & 1:
            stream.append(0)
    return bytes(stream)


class BitKnit2DecoderTests(unittest.TestCase):
    def test_empty_output_needs_no_source_words(self):
        self.assertEqual(decompress_bitknit2(b"", 0), b"")

    def test_decodes_an_odd_length_raw_quantum(self):
        expected = b"clean"
        self.assertEqual(
            decompress_bitknit2(raw_bitknit2(expected), len(expected)),
            expected,
        )

    def test_decodes_multiple_raw_quanta(self):
        first = bytes(range(256)) * 256
        second = b"MIT"
        expected = first + second
        self.assertEqual(
            decompress_bitknit2(raw_bitknit2(first, second), len(expected)),
            expected,
        )

    def test_rejects_bad_magic(self):
        with self.assertRaisesRegex(ValueError, "bad magic word"):
            decompress_bitknit2(b"\0\0", 1)

    def test_rejects_truncated_source(self):
        with self.assertRaisesRegex(ValueError, "source underflow"):
            decompress_bitknit2(struct.pack("<H", BITKNIT2_MAGIC), 1)


if __name__ == "__main__":
    unittest.main()

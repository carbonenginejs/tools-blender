import struct
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons" / "carbon_eve_resources" / "gr2_importer"))

from gr2 import read_raw  # noqa: E402


MAGIC_32 = bytes.fromhex("29de6cc0baa4532b25f5b7a5f666e2ee")


def create_minimal_gr2() -> bytes:
    section_directory_offset = 68
    section_data_offset = 112
    section_size = 48
    pointer_fixup_offset = section_data_offset + section_size
    data = bytearray(pointer_fixup_offset + 12)
    data[:16] = MAGIC_32

    struct.pack_into("<I", data, 32, 7)
    struct.pack_into("<I", data, 44, section_directory_offset - 32)
    struct.pack_into("<I", data, 48, 1)
    struct.pack_into("<I", data, 52, 0)
    struct.pack_into("<I", data, 56, 0)
    struct.pack_into("<I", data, 60, 0)
    struct.pack_into("<I", data, 64, 36)

    struct.pack_into("<I", data, section_directory_offset, 0)
    struct.pack_into("<I", data, section_directory_offset + 4, section_data_offset)
    struct.pack_into("<I", data, section_directory_offset + 8, section_size)
    struct.pack_into("<I", data, section_directory_offset + 12, section_size)
    struct.pack_into("<I", data, section_directory_offset + 28, pointer_fixup_offset)
    struct.pack_into("<I", data, section_directory_offset + 32, 1)

    struct.pack_into("<I", data, section_data_offset, 20)
    struct.pack_into("<i", data, section_data_offset + 12, 1)
    struct.pack_into("<I", data, section_data_offset + 36, 42)
    data[section_data_offset + 40 : section_data_offset + 46] = b"caf\xc3\xa9\0"

    struct.pack_into("<III", data, pointer_fixup_offset, 4, 0, 40)
    return bytes(data)


class ReaderTests(unittest.TestCase):
    def test_reads_minimal_reflected_file_and_utf8_member_name(self):
        result = read_raw(create_minimal_gr2())

        self.assertEqual(result.version, 7)
        self.assertEqual(result.section_count, 1)
        self.assertEqual(result.file_info["caf\xe9"], 42)

    def test_accepts_file_like_objects(self):
        from io import BytesIO

        self.assertEqual(read_raw(BytesIO(create_minimal_gr2())).file_info["caf\xe9"], 42)


if __name__ == "__main__":
    unittest.main()

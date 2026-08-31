"""Bounds-checked little-endian primitives for CMF."""

from __future__ import annotations

import struct
from contextlib import contextmanager


class CmfError(ValueError):
    """A malformed or unsupported CMF document."""


def source_bytes(source) -> bytes:
    if isinstance(source, (bytes, bytearray, memoryview)):
        return bytes(source)
    if hasattr(source, "read"):
        value = source.read()
        if not isinstance(value, (bytes, bytearray, memoryview)):
            raise TypeError("CMF file-like objects must return bytes")
        return bytes(value)
    with open(source, "rb") as stream:
        return stream.read()


class BinaryReader:
    def __init__(self, data: bytes):
        self.bytes = memoryview(data).cast("B")
        self.span_region: tuple[int, int] | None = None
        self.strict_spans = True

    @contextmanager
    def spans_within(self, start: int, end: int):
        previous = self.span_region
        self.span_region = (start, end)
        try:
            yield
        finally:
            self.span_region = previous

    def require(self, offset: int, size: int, label: str = "data") -> None:
        if offset < 0 or size < 0 or offset + size > len(self.bytes):
            raise CmfError(
                f"CMF {label} range {offset}..{offset + size} exceeds file size {len(self.bytes)}"
            )

    def _unpack(self, code: str, offset: int):
        size = struct.calcsize(code)
        self.require(offset, size)
        return struct.unpack_from(code, self.bytes, offset)[0]

    def u8(self, offset: int) -> int:
        return self._unpack("<B", offset)

    def i8(self, offset: int) -> int:
        return self._unpack("<b", offset)

    def u16(self, offset: int) -> int:
        return self._unpack("<H", offset)

    def i16(self, offset: int) -> int:
        return self._unpack("<h", offset)

    def u32(self, offset: int) -> int:
        return self._unpack("<I", offset)

    def i64(self, offset: int) -> int:
        return self._unpack("<q", offset)

    def u64(self, offset: int) -> int:
        return self._unpack("<Q", offset)

    def f16(self, offset: int) -> float:
        return self._unpack("<e", offset)

    def f32(self, offset: int) -> float:
        return self._unpack("<f", offset)

    def bytes_at(self, offset: int, size: int) -> memoryview:
        self.require(offset, size)
        return self.bytes[offset : offset + size]

    def string(self, offset: int, size: int) -> str:
        try:
            return bytes(self.bytes_at(offset, size)).decode("utf-8")
        except UnicodeDecodeError as error:
            raise CmfError(f"CMF string at {offset} is not valid UTF-8") from error


def enum_name(values, index: int, label: str) -> str:
    try:
        return values[index]
    except IndexError as error:
        raise CmfError(f"CMF {label} has unknown value {index}") from error


def read_bounds(reader: BinaryReader, offset: int) -> dict:
    return {
        "min": [reader.f32(offset + component * 4) for component in range(3)],
        "max": [reader.f32(offset + 12 + component * 4) for component in range(3)],
    }


def read_vector3(reader: BinaryReader, offset: int) -> list[float]:
    return [reader.f32(offset + component * 4) for component in range(3)]


def read_quaternion(reader: BinaryReader, offset: int) -> list[float]:
    return [reader.f32(offset + component * 4) for component in range(4)]


def read_matrix(reader: BinaryReader, offset: int) -> list[float]:
    return [reader.f32(offset + component * 4) for component in range(16)]

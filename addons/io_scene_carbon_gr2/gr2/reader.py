"""Low-level pure-Python Granny 2 container and reflection reader."""

from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from typing import Any

from .codecs import decompress_bitknit2, decompress_oodle1


MEMBER_TYPES = {
    "End": 0,
    "Inline": 1,
    "Reference": 2,
    "ReferenceToArray": 3,
    "ArrayOfReferences": 4,
    "VariantReference": 5,
    "UnsupportedRemove": 6,
    "ReferenceToVariantArray": 7,
    "String": 8,
    "Transform": 9,
    "Real32": 10,
    "Int8": 11,
    "UInt8": 12,
    "BinormalInt8": 13,
    "NormalUInt8": 14,
    "Int16": 15,
    "UInt16": 16,
    "BinormalInt16": 17,
    "NormalUInt16": 18,
    "Int32": 19,
    "UInt32": 20,
    "Real16": 21,
    "EmptyReference": 22,
}

GR2_MAGICS = {
    "29de6cc0baa4532b25f5b7a5f666e2ee": 4,
    "e59b495e6f631f141e13eba990beedc4": 8,
}

GR2_COMPRESSION_NONE = 0
GR2_COMPRESSION_OODLE0 = 1
GR2_COMPRESSION_OODLE1 = 2
GR2_COMPRESSION_BITKNIT2 = 4
GRANNY_TRANSFORM_SIZE = 68


class TypedList(list):
    """A reflected array carrying its Granny member definitions."""

    granny_type: list[dict[str, Any]]

    def __init__(self, values=(), *, granny_type=None):
        super().__init__(values)
        self.granny_type = granny_type or []


@dataclass(frozen=True)
class RawGr2:
    version: int
    section_count: int
    file_info: dict[str, Any]

    @property
    def secCount(self) -> int:  # compatibility with the JavaScript contract
        return self.section_count

    @property
    def fileInfo(self) -> dict[str, Any]:
        return self.file_info


def _half_to_float(value: int) -> float:
    sign = -1.0 if value & 0x8000 else 1.0
    exponent = (value & 0x7C00) >> 10
    fraction = value & 0x03FF
    if exponent == 0:
        return sign * 2**-14 * (fraction / 1024)
    if exponent == 0x1F:
        return math.nan if fraction else sign * math.inf
    return sign * 2 ** (exponent - 15) * (1 + fraction / 1024)


def decompress_section(
    compression: int,
    data: bytes | bytearray | memoryview,
    expanded_size: int,
    section: dict[str, int],
) -> bytes:
    if compression == GR2_COMPRESSION_NONE:
        return bytes(data)
    if compression in (GR2_COMPRESSION_OODLE0, GR2_COMPRESSION_OODLE1):
        return decompress_oodle1(
            data,
            expanded_size,
            first16=section["first16"],
            first8=section["first8"],
        )
    if compression == GR2_COMPRESSION_BITKNIT2:
        return decompress_bitknit2(data, expanded_size)
    raise ValueError(
        f"section needs codec: format {compression} (0=None,2=Oodle1,4=BitKnit2)"
    )


def _coerce_bytes(source: Any) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    if isinstance(source, memoryview):
        return source.tobytes()
    if hasattr(source, "read"):
        return source.read()
    raise TypeError("GR2 input must be bytes or a binary file-like object")


def read_raw(source: Any) -> RawGr2:
    """Parse GR2/GSF bytes into the reflected shared object graph."""

    data = _coerce_bytes(source)
    view = memoryview(data)
    magic = bytes(view[:16]).hex()
    pointer_size = GR2_MAGICS.get(magic)
    if not pointer_size:
        raise ValueError(f"unknown gr2 magic {magic}")

    def file_u32(offset: int) -> int:
        return struct.unpack_from("<I", view, offset)[0]

    member_definition_size = 20 + 3 * pointer_size
    header = 32
    version = file_u32(header)
    section_base_offset = header + file_u32(header + 12)
    section_count = file_u32(header + 16)
    root_type_section = file_u32(header + 20)
    root_type_offset = file_u32(header + 24)
    root_object_section = file_u32(header + 28)
    root_object_offset = file_u32(header + 32)

    sections: list[dict[str, int]] = []
    for index in range(section_count):
        base = section_base_offset + index * 44
        sections.append(
            {
                "format": file_u32(base),
                "data_offset": file_u32(base + 4),
                "data_size": file_u32(base + 8),
                "expanded_size": file_u32(base + 12),
                "first16": file_u32(base + 20),
                "first8": file_u32(base + 24),
                "pointer_fixup_offset": file_u32(base + 28),
                "pointer_fixup_count": file_u32(base + 32),
            }
        )

    section_bases: list[int] = []
    expanded_sections: list[bytes] = []
    total = 0
    for section in sections:
        section_bases.append(total)
        start = section["data_offset"]
        end = start + section["data_size"]
        expanded = decompress_section(
            section["format"],
            view[start:end],
            section["expanded_size"],
            section,
        )
        if len(expanded) != section["expanded_size"]:
            raise ValueError("GR2 section expanded to an unexpected size")
        expanded_sections.append(expanded)
        total += section["expanded_size"]

    memory = b"".join(expanded_sections)
    memory_view = memoryview(memory)

    def u32(offset: int) -> int:
        return struct.unpack_from("<I", memory_view, offset)[0]

    def i32(offset: int) -> int:
        return struct.unpack_from("<i", memory_view, offset)[0]

    def f32(offset: int) -> float:
        return struct.unpack_from("<f", memory_view, offset)[0]

    relocations: dict[int, int] = {}
    for section_index, section in enumerate(sections):
        fixup_count = section["pointer_fixup_count"]
        if not fixup_count:
            continue
        fixup_offset = section["pointer_fixup_offset"]
        if section["format"] == GR2_COMPRESSION_BITKNIT2:
            compressed_size = file_u32(fixup_offset)
            fixups = decompress_bitknit2(
                view[fixup_offset + 4 : fixup_offset + 4 + compressed_size],
                fixup_count * 12,
            )
        else:
            fixups = bytes(view[fixup_offset : fixup_offset + fixup_count * 12])

        for fixup_index in range(fixup_count):
            source_offset, target_section, target_offset = struct.unpack_from(
                "<III", fixups, fixup_index * 12
            )
            relocations[section_bases[section_index] + source_offset] = (
                section_bases[target_section] + target_offset
            )

    null_pointer = -1

    def pointer(global_offset: int) -> int:
        return relocations.get(global_offset, null_pointer)

    def read_string(global_offset: int) -> str | None:
        if global_offset < 0:
            return None
        end = memory.find(b"\0", global_offset)
        if end < 0:
            end = len(memory)
        return memory[global_offset:end].decode("utf-8", errors="replace")

    type_cache: dict[int, list[dict[str, Any]]] = {}

    def read_type(global_offset: int) -> list[dict[str, Any]]:
        if global_offset in type_cache:
            return type_cache[global_offset]
        members: list[dict[str, Any]] = []
        cursor = global_offset
        while True:
            member_type = u32(cursor)
            if member_type == MEMBER_TYPES["End"]:
                break
            members.append(
                {
                    "type": member_type,
                    "name": read_string(pointer(cursor + 4)),
                    "ref_type": pointer(cursor + 4 + pointer_size),
                    "array_width": i32(cursor + 4 + 2 * pointer_size),
                }
            )
            cursor += member_definition_size
            if len(members) > 4096:
                raise ValueError("type member overflow")
        type_cache[global_offset] = members
        return members

    size_cache: dict[int, int] = {}

    def member_size(member: dict[str, Any]) -> int:
        width = member["array_width"] if member["array_width"] > 0 else 1
        member_type = member["type"]
        if member_type == MEMBER_TYPES["Inline"]:
            return object_size(member["ref_type"]) * width
        if member_type in (MEMBER_TYPES["Reference"], MEMBER_TYPES["String"]):
            return pointer_size
        if member_type == MEMBER_TYPES["EmptyReference"]:
            return 4
        if member_type in (
            MEMBER_TYPES["ReferenceToArray"],
            MEMBER_TYPES["ArrayOfReferences"],
        ):
            return 4 + pointer_size
        if member_type == MEMBER_TYPES["VariantReference"]:
            return 2 * pointer_size
        if member_type == MEMBER_TYPES["ReferenceToVariantArray"]:
            return 2 * pointer_size + 4
        if member_type == MEMBER_TYPES["Transform"]:
            return GRANNY_TRANSFORM_SIZE
        if member_type in (
            MEMBER_TYPES["Real32"],
            MEMBER_TYPES["Int32"],
            MEMBER_TYPES["UInt32"],
        ):
            return 4 * width
        if member_type in (
            MEMBER_TYPES["Int16"],
            MEMBER_TYPES["UInt16"],
            MEMBER_TYPES["BinormalInt16"],
            MEMBER_TYPES["NormalUInt16"],
            MEMBER_TYPES["Real16"],
        ):
            return 2 * width
        if member_type in (
            MEMBER_TYPES["Int8"],
            MEMBER_TYPES["UInt8"],
            MEMBER_TYPES["BinormalInt8"],
            MEMBER_TYPES["NormalUInt8"],
        ):
            return width
        return 0

    def object_size(type_offset: int) -> int:
        if type_offset in size_cache:
            return size_cache[type_offset]
        size_cache[type_offset] = 0
        size = sum(member_size(member) for member in read_type(type_offset))
        size_cache[type_offset] = size
        return size

    layout_cache: dict[int, tuple[list[dict[str, Any]], list[int]]] = {}

    def type_layout(type_offset: int):
        if type_offset in layout_cache:
            return layout_cache[type_offset]
        members = read_type(type_offset)
        offsets = []
        size = 0
        for member in members:
            offsets.append(size)
            size += member_size(member)
        layout_cache[type_offset] = (members, offsets)
        return members, offsets

    def numeric_stride(member_type: int) -> int:
        if member_type in (
            MEMBER_TYPES["Real32"],
            MEMBER_TYPES["Int32"],
            MEMBER_TYPES["UInt32"],
        ):
            return 4
        if member_type in (
            MEMBER_TYPES["Int16"],
            MEMBER_TYPES["UInt16"],
            MEMBER_TYPES["BinormalInt16"],
            MEMBER_TYPES["NormalUInt16"],
            MEMBER_TYPES["Real16"],
        ):
            return 2
        return 1

    def numeric_at(member_type: int, offset: int):
        if member_type == MEMBER_TYPES["Real32"]:
            return f32(offset)
        if member_type == MEMBER_TYPES["Int32"]:
            return i32(offset)
        if member_type == MEMBER_TYPES["UInt32"]:
            return u32(offset)
        if member_type in (MEMBER_TYPES["Int16"], MEMBER_TYPES["BinormalInt16"]):
            return struct.unpack_from("<h", memory_view, offset)[0]
        if member_type in (MEMBER_TYPES["UInt16"], MEMBER_TYPES["NormalUInt16"]):
            return struct.unpack_from("<H", memory_view, offset)[0]
        if member_type == MEMBER_TYPES["Real16"]:
            return _half_to_float(struct.unpack_from("<H", memory_view, offset)[0])
        if member_type in (MEMBER_TYPES["Int8"], MEMBER_TYPES["BinormalInt8"]):
            return struct.unpack_from("<b", memory_view, offset)[0]
        if member_type in (MEMBER_TYPES["UInt8"], MEMBER_TYPES["NormalUInt8"]):
            return memory_view[offset]
        raise ValueError(f"unsupported numeric member type {member_type}")

    def numeric(member: dict[str, Any], offset: int):
        width = member["array_width"] if member["array_width"] > 0 else 1
        if width == 1:
            return numeric_at(member["type"], offset)
        stride = numeric_stride(member["type"])
        return [numeric_at(member["type"], offset + index * stride) for index in range(width)]

    def read_transform(offset: int) -> dict[str, Any]:
        return {
            "flags": u32(offset),
            "position": [f32(offset + 4), f32(offset + 8), f32(offset + 12)],
            "orientation": [
                f32(offset + 16),
                f32(offset + 20),
                f32(offset + 24),
                f32(offset + 28),
            ],
            "scaleShear": [f32(offset + 32 + index * 4) for index in range(9)],
        }

    object_cache: dict[tuple[int, int], dict[str, Any]] = {}
    depth = 0

    def read_object(type_offset: int, object_offset: int):
        nonlocal depth
        if type_offset < 0 or object_offset < 0:
            return None
        key = (type_offset, object_offset)
        if key in object_cache:
            return object_cache[key]
        depth += 1
        if depth > 200:
            depth -= 1
            raise ValueError("recursion too deep")

        result: dict[str, Any] = {}
        object_cache[key] = result
        members, offsets = type_layout(type_offset)
        for member, relative_offset in zip(members, offsets):
            field = object_offset + relative_offset
            name = member["name"] or "_"
            if name in result:
                suffix = 2
                while f"{name}\0{suffix}" in result:
                    suffix += 1
                name = f"{name}\0{suffix}"
            member_type = member["type"]

            if member_type == MEMBER_TYPES["String"]:
                value = read_string(pointer(field))
            elif member_type == MEMBER_TYPES["Reference"]:
                value = read_object(member["ref_type"], pointer(field))
            elif member_type == MEMBER_TYPES["Transform"]:
                value = read_transform(field)
            elif member_type == MEMBER_TYPES["Inline"]:
                width = member["array_width"] if member["array_width"] > 0 else 1
                stride = object_size(member["ref_type"])
                if member["array_width"] > 1:
                    value = [
                        read_object(member["ref_type"], field + index * stride)
                        for index in range(width)
                    ]
                else:
                    value = read_object(member["ref_type"], field)
            elif member_type == MEMBER_TYPES["ReferenceToArray"]:
                count = i32(field)
                target = pointer(field + 4)
                stride = object_size(member["ref_type"])
                value = [
                    read_object(member["ref_type"], target + index * stride)
                    for index in range(count)
                ]
            elif member_type == MEMBER_TYPES["ArrayOfReferences"]:
                count = i32(field)
                target = pointer(field + 4)
                value = [
                    read_object(member["ref_type"], pointer(target + index * pointer_size))
                    for index in range(count)
                ]
            elif member_type == MEMBER_TYPES["VariantReference"]:
                value = read_object(pointer(field), pointer(field + pointer_size))
            elif member_type == MEMBER_TYPES["ReferenceToVariantArray"]:
                variant_type = pointer(field)
                count = i32(field + pointer_size)
                target = pointer(field + pointer_size + 4)
                stride = object_size(variant_type) if variant_type >= 0 else 0
                value = TypedList(
                    [read_object(variant_type, target + index * stride) for index in range(count)],
                    granny_type=read_type(variant_type) if variant_type >= 0 else [],
                )
            elif member_type in (
                MEMBER_TYPES["Real32"],
                MEMBER_TYPES["Int8"],
                MEMBER_TYPES["UInt8"],
                MEMBER_TYPES["BinormalInt8"],
                MEMBER_TYPES["NormalUInt8"],
                MEMBER_TYPES["Int16"],
                MEMBER_TYPES["UInt16"],
                MEMBER_TYPES["BinormalInt16"],
                MEMBER_TYPES["NormalUInt16"],
                MEMBER_TYPES["Int32"],
                MEMBER_TYPES["UInt32"],
                MEMBER_TYPES["Real16"],
            ):
                value = numeric(member, field)
            else:
                value = None
            result[name] = value

        depth -= 1
        return result

    root_type_global = section_bases[root_type_section] + root_type_offset
    root_object_global = section_bases[root_object_section] + root_object_offset
    file_info = read_object(root_type_global, root_object_global)
    return RawGr2(version, section_count, file_info)


__all__ = [
    "GR2_MAGICS",
    "MEMBER_TYPES",
    "RawGr2",
    "TypedList",
    "decompress_section",
    "read_raw",
]

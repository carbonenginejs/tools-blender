"""Pure-Python CMF v1 schema and geometry reader."""

from __future__ import annotations

import struct
import zlib

from .binary import (
    BinaryReader,
    CmfError,
    enum_name,
    read_bounds,
    read_matrix,
    read_quaternion,
    read_vector3,
    source_bytes,
)
from .constants import (
    ANIMATION_CHANNEL_TARGET_TYPE,
    ELEMENT_TYPE,
    ELEMENT_TYPE_SIZE,
    FILE_SIGNATURE,
    FILE_VERSION,
    INTERPOLATION,
    MESH_TOPOLOGY,
    SECTION_COMPRESSION,
    SECTION_TYPE,
    STRUCT_SIZE,
    USAGE,
)
from .meshopt import decode_index_buffer, decode_vertex_buffer
from .tangents import unpack_packed_tangents


CHANNEL_NAMES = {
    "Position": "position",
    "Normal": "normal",
    "Tangent": "tangent",
    "Binormal": "binormal",
    "TexCoord": "texcoord",
    "Color": "color",
    "BoneIndices": "blendIndice",
    "BoneWeights": "blendWeight",
    "PackedTangent": "packedTangent",
    "PackedTangentLegacy": "packedTangentLegacy",
}


def read_cmf(
    source,
    *,
    validate_crc: bool = True,
    decode_buffers: bool = True,
    strict_spans: bool = True,
    unpack_tangents: bool = True,
) -> dict:
    """Read CMF v1 bytes into the canonical native dictionary graph."""

    data = source_bytes(source)
    reader = BinaryReader(data)
    reader.strict_spans = strict_spans
    header = _read_header(reader)
    _validate_header(reader, header, validate_crc=validate_crc)
    data_section = header["sections"][0]
    with reader.spans_within(
        data_section["offset"], data_section["offset"] + data_section["compressedSize"]
    ):
        root = _read_data(reader, data_section["offset"])
    metadata_section = next(
        (section for section in header["sections"] if section["type"] == "Metadata"),
        None,
    )
    metadata = None
    if metadata_section:
        with reader.spans_within(
            metadata_section["offset"],
            metadata_section["offset"] + metadata_section["compressedSize"],
        ):
            metadata = _read_metadata(reader, metadata_section["offset"])
    result = {
        "signature": header["signature"],
        "version": header["version"],
        "headerSize": header["headerSize"],
        "crc32": header["crc32"],
        "sections": header["sections"],
        "metadata": metadata,
        "meshes": root["meshes"],
        "skeletons": root["skeletons"],
        "animations": root["animations"],
    }
    _validate_graph_structure(result)
    if decode_buffers:
        _decode_geometry(result, reader.bytes, unpack_tangents=unpack_tangents)
    return result


def inspect_cmf(source, *, validate_crc: bool = True) -> dict:
    result = read_cmf(source, validate_crc=validate_crc, decode_buffers=False)
    return {
        "signature": result["signature"],
        "version": result["version"],
        "headerSize": result["headerSize"],
        "crc32": result["crc32"],
        "sections": [
            {
                "index": index,
                "type": section["type"],
                "compression": section["compression"],
                "compressedSize": section["compressedSize"],
                "uncompressedSize": section["uncompressedSize"],
                "gpuAlignment": section["gpuAlignment"],
            }
            for index, section in enumerate(result["sections"])
        ],
        "metadataEntries": len((result["metadata"] or {}).get("entries") or []),
        "meshes": [
            {
                "name": mesh["name"],
                "topology": mesh["topology"],
                "lods": len(mesh["lods"]),
                "areas": len(mesh["areas"]),
                "vertexElements": len(mesh["decl"]),
                "morphTargets": len(mesh["morphTargets"]["targets"]),
                "skeleton": mesh["skeleton"],
            }
            for mesh in result["meshes"]
        ],
        "skeletons": [
            {
                "name": skeleton["name"],
                "bones": len(skeleton["bones"]),
                "boneMasks": len(skeleton["boneMasks"]),
            }
            for skeleton in result["skeletons"]
        ],
        "animations": [
            {
                "name": animation["name"],
                "duration": animation["duration"],
                "channels": len(animation["channels"]),
                "curves": len(animation["curves"]),
            }
            for animation in result["animations"]
        ],
    }


def _read_header(reader: BinaryReader) -> dict:
    header_size = reader.u32(8)
    reader.require(0, min(header_size, len(reader.bytes)), "header")
    with reader.spans_within(0, header_size):
        sections = _read_array(
            reader,
            _read_span(reader, 16, STRUCT_SIZE["Section"]),
            _read_section,
        )
    return {
        "signature": reader.u32(0),
        "version": reader.u32(4),
        "headerSize": header_size,
        "crc32": reader.u32(12),
        "sections": sections,
    }


def _validate_header(reader: BinaryReader, header: dict, *, validate_crc: bool) -> None:
    if header["signature"] != FILE_SIGNATURE:
        raise CmfError(f"Invalid CMF signature 0x{header['signature']:08x}")
    if header["version"] != FILE_VERSION:
        raise CmfError(f"Unsupported CMF version {header['version']}")
    if header["headerSize"] < STRUCT_SIZE["Header"]:
        raise CmfError("CMF headerSize is smaller than the fixed header")
    if header["headerSize"] > len(reader.bytes):
        raise CmfError("CMF headerSize exceeds file size")
    sections = header["sections"]
    if not sections:
        raise CmfError("CMF header contains no sections")
    if sections[0]["type"] != "Data":
        raise CmfError("CMF first section must be Data")
    previous_end = header["headerSize"]
    data_count = 0
    for index, section in enumerate(sections):
        reader.require(section["offset"], section["compressedSize"], f"section {index}")
        if section["offset"] < previous_end:
            raise CmfError(f"CMF section {index} overlaps the header or preceding section")
        previous_end = section["offset"] + section["compressedSize"]
        if section["type"] == "Data":
            data_count += 1
        if section["type"] == "Metadata" and index != len(sections) - 1:
            raise CmfError("CMF Metadata section must be last")
        if section["type"] != "GpuBuffer" and section["compression"] != "None":
            raise CmfError(f"CMF {section['type']} section cannot use {section['compression']}")
        if section["compression"] == "None":
            if section["compressedSize"] != section["uncompressedSize"]:
                raise CmfError(f"CMF uncompressed section {index} has different stored sizes")
        else:
            alignment = section["gpuAlignment"]
            if alignment == 0 or section["uncompressedSize"] % alignment:
                raise CmfError(f"CMF compressed section {index} has invalid GPU alignment")
            if section["compression"] == "MeshOptimizerVertexBuffer" and not (
                4 <= alignment <= 256 and alignment % 4 == 0
            ):
                raise CmfError(f"CMF meshoptimizer vertex section {index} has invalid stride")
            if section["compression"] == "MeshOptimizerIndexBuffer":
                if alignment not in (2, 4):
                    raise CmfError(f"CMF meshoptimizer index section {index} has invalid stride")
                if section["uncompressedSize"] // alignment % 3:
                    raise CmfError(f"CMF meshoptimizer index section {index} is not triangles")
    if data_count != 1:
        raise CmfError("CMF must contain exactly one Data section")
    if validate_crc and header["crc32"]:
        actual = zlib.crc32(reader.bytes[16:]) & 0xFFFFFFFF
        if actual != header["crc32"]:
            raise CmfError(
                f"CMF CRC mismatch: expected 0x{header['crc32']:08x}, got 0x{actual:08x}"
            )


def _read_section(reader: BinaryReader, offset: int) -> dict:
    return {
        "offset": reader.u32(offset),
        "compressedSize": reader.u32(offset + 4),
        "uncompressedSize": reader.u32(offset + 8),
        "gpuAlignment": reader.u16(offset + 12),
        "type": enum_name(SECTION_TYPE, reader.u8(offset + 14), "section type"),
        "compression": enum_name(
            SECTION_COMPRESSION,
            reader.u8(offset + 15),
            "section compression",
        ),
    }


def _read_data(reader: BinaryReader, offset: int) -> dict:
    return {
        "meshes": _read_array(
            reader, _read_span(reader, offset, STRUCT_SIZE["Mesh"]), _read_mesh
        ),
        "skeletons": _read_array(
            reader,
            _read_span(reader, offset + 16, STRUCT_SIZE["Skeleton"]),
            _read_skeleton,
        ),
        "animations": _read_array(
            reader,
            _read_span(reader, offset + 32, STRUCT_SIZE["Animation"]),
            _read_animation,
        ),
    }


def _read_metadata(reader: BinaryReader, offset: int) -> dict:
    return {
        "entries": _read_array(
            reader,
            _read_span(reader, offset, STRUCT_SIZE["MetadataEntry"]),
            _read_metadata_entry,
        )
    }


def _read_metadata_entry(reader: BinaryReader, offset: int) -> dict:
    return {"key": _read_string(reader, offset), "value": _read_string(reader, offset + 16)}


def _read_mesh(reader: BinaryReader, offset: int) -> dict:
    return {
        "name": _read_string(reader, offset),
        "decl": _read_array(
            reader,
            _read_span(reader, offset + 16, STRUCT_SIZE["VertexElement"]),
            _read_vertex_element,
        ),
        "lods": _read_array(
            reader,
            _read_span(reader, offset + 32, STRUCT_SIZE["MeshLod"]),
            _read_mesh_lod,
        ),
        "areas": _read_array(
            reader,
            _read_span(reader, offset + 48, STRUCT_SIZE["MeshArea"]),
            _read_mesh_area,
        ),
        "boneBindings": _read_array(
            reader,
            _read_span(reader, offset + 64, STRUCT_SIZE["BoneBinding"]),
            _read_bone_binding,
        ),
        "morphTargets": _read_morph_targets(reader, offset + 80),
        "uvDensities": _read_float_array(reader, _read_span(reader, offset + 112, 4)),
        "bounds": read_bounds(reader, offset + 128),
        "audioOcclusionMesh": _read_audio_occlusion_mesh(reader, offset + 152),
        "topology": enum_name(MESH_TOPOLOGY, reader.u8(offset + 208), "mesh topology"),
        "skeleton": None if reader.u8(offset + 209) == 0xFF else reader.u8(offset + 209),
    }


def _read_vertex_element(reader: BinaryReader, offset: int) -> dict:
    return {
        "usage": enum_name(USAGE, reader.u8(offset), "vertex usage"),
        "usageIndex": reader.u8(offset + 1),
        "type": enum_name(ELEMENT_TYPE, reader.u8(offset + 2), "element type"),
        "elementCount": reader.u8(offset + 3),
        "offset": reader.u32(offset + 4),
    }


def _read_mesh_area(reader: BinaryReader, offset: int) -> dict:
    return {
        "name": _read_string(reader, offset),
        "bounds": read_bounds(reader, offset + 16),
        "bones": _read_uint16_array(reader, _read_span(reader, offset + 40, 2)),
        "affectedByBones": bool(reader.u8(offset + 56)),
        "affectedByMorphTargets": bool(reader.u8(offset + 57)),
    }


def _read_lod_mesh_area(reader: BinaryReader, offset: int) -> dict:
    return {"firstElement": reader.u32(offset), "elementCount": reader.u32(offset + 4)}


def _read_bone_binding(reader: BinaryReader, offset: int) -> dict:
    return {"name": _read_string(reader, offset), "bounds": read_bounds(reader, offset + 16)}


def _read_morph_targets(reader: BinaryReader, offset: int) -> dict:
    return {
        "decl": _read_array(
            reader,
            _read_span(reader, offset, STRUCT_SIZE["VertexElement"]),
            _read_vertex_element,
        ),
        "targets": _read_array(
            reader,
            _read_span(reader, offset + 16, STRUCT_SIZE["MorphTarget"]),
            _read_morph_target,
        ),
    }


def _read_morph_target(reader: BinaryReader, offset: int) -> dict:
    return {"name": _read_string(reader, offset), "maxDisplacement": reader.f32(offset + 16)}


def _read_mesh_lod(reader: BinaryReader, offset: int) -> dict:
    return {
        "vb": _read_buffer_view(reader, offset),
        "ib": _read_buffer_view(reader, offset + 16),
        "areas": _read_array(
            reader,
            _read_span(reader, offset + 32, STRUCT_SIZE["LodMeshArea"]),
            _read_lod_mesh_area,
        ),
        "morphTargets": _read_array(
            reader,
            _read_span(reader, offset + 48, STRUCT_SIZE["LodMorphTarget"]),
            _read_lod_morph_target,
        ),
        "threshold": reader.u32(offset + 64),
    }


def _read_lod_morph_target(reader: BinaryReader, offset: int) -> dict:
    return {"vb": _read_buffer_view(reader, offset)}


def _read_audio_occlusion_mesh(reader: BinaryReader, offset: int) -> dict:
    return {
        "vertices": _read_array(reader, _read_span(reader, offset, 12), read_vector3),
        "indices": _read_uint16_array(reader, _read_span(reader, offset + 16, 2)),
        "bounds": read_bounds(reader, offset + 32),
    }


def _read_skeleton(reader: BinaryReader, offset: int) -> dict:
    return {
        "name": _read_string(reader, offset),
        "bones": _read_array(reader, _read_span(reader, offset + 16, 16), _read_string),
        "parents": _read_uint32_array(reader, _read_span(reader, offset + 32, 4)),
        "restTransforms": _read_array(
            reader,
            _read_span(reader, offset + 48, STRUCT_SIZE["Transform"]),
            _read_transform,
        ),
        "invBindTransforms": _read_array(
            reader, _read_span(reader, offset + 64, 64), read_matrix
        ),
        "boneMasks": _read_array(
            reader,
            _read_span(reader, offset + 80, STRUCT_SIZE["BoneMask"]),
            _read_bone_mask,
        ),
    }


def _read_transform(reader: BinaryReader, offset: int) -> dict:
    return {
        "position": read_vector3(reader, offset),
        "rotation": read_quaternion(reader, offset + 12),
        "scale": read_vector3(reader, offset + 28),
    }


def _read_bone_mask(reader: BinaryReader, offset: int) -> dict:
    return {
        "name": _read_string(reader, offset),
        "weights": _read_array(
            reader,
            _read_span(reader, offset + 16, STRUCT_SIZE["BoneWeight"]),
            _read_bone_weight,
        ),
    }


def _read_bone_weight(reader: BinaryReader, offset: int) -> dict:
    return {"index": reader.u32(offset), "weight": reader.f32(offset + 4)}


def _read_animation(reader: BinaryReader, offset: int) -> dict:
    return {
        "name": _read_string(reader, offset),
        "channels": _read_array(
            reader,
            _read_span(reader, offset + 16, STRUCT_SIZE["AnimationChannel"]),
            _read_animation_channel,
        ),
        "curves": _read_array(
            reader,
            _read_span(reader, offset + 32, STRUCT_SIZE["AnimationCurve"]),
            _read_animation_curve,
        ),
        "duration": reader.f32(offset + 48),
    }


def _read_animation_channel(reader: BinaryReader, offset: int) -> dict:
    return {
        "target": _read_string(reader, offset),
        "targetType": enum_name(
            ANIMATION_CHANNEL_TARGET_TYPE,
            reader.u8(offset + 16),
            "animation target type",
        ),
        "curveIndex": reader.u32(offset + 20),
    }


def _read_animation_curve(reader: BinaryReader, offset: int) -> dict:
    return {
        "valueDimension": reader.u8(offset),
        "interpolation": enum_name(
            INTERPOLATION, reader.u8(offset + 1), "animation interpolation"
        ),
        "knotType": enum_name(ELEMENT_TYPE, reader.u8(offset + 2), "knot type"),
        "valueType": enum_name(ELEMENT_TYPE, reader.u8(offset + 3), "value type"),
        "knotCount": reader.u32(offset + 4),
        "knots": _read_byte_array(reader, _read_span(reader, offset + 8, 1)),
        "values": _read_byte_array(reader, _read_span(reader, offset + 24, 1)),
    }


def _read_buffer_view(reader: BinaryReader, offset: int) -> dict:
    return {
        "index": reader.u32(offset),
        "offset": reader.u32(offset + 4),
        "size": reader.u32(offset + 8),
        "stride": reader.u32(offset + 12),
    }


def _read_span(reader: BinaryReader, offset: int, element_size: int) -> dict:
    raw_offset = reader.i64(offset)
    byte_size = reader.u64(offset + 8)
    is_relative = bool(raw_offset & 1)
    if byte_size and reader.strict_spans and not is_relative:
        raise CmfError("CMF serialized spans must use self-relative offsets")
    data_offset = None if byte_size == 0 else offset + (raw_offset & ~1) if is_relative else raw_offset
    if byte_size % element_size:
        raise CmfError(
            f"CMF span byteSize {byte_size} is not a multiple of element size {element_size}"
        )
    if data_offset is not None:
        reader.require(data_offset, byte_size, "span")
        if reader.span_region is not None:
            start, end = reader.span_region
            if data_offset < start or data_offset + byte_size > end:
                raise CmfError(
                    f"CMF span {data_offset}..{data_offset + byte_size} escapes its containing region"
                )
    return {
        "offset": data_offset,
        "byteSize": byte_size,
        "count": byte_size // element_size,
        "elementSize": element_size,
        "addressMode": "offset" if is_relative else "pointer",
    }


def _read_array(reader: BinaryReader, span: dict, read_element) -> list:
    if not span["count"]:
        return []
    return [
        read_element(reader, span["offset"] + index * span["elementSize"])
        for index in range(span["count"])
    ]


def _read_string(reader: BinaryReader, offset: int) -> str:
    span = _read_span(reader, offset, 1)
    return "" if span["offset"] is None else reader.string(span["offset"], span["byteSize"])


def _read_byte_array(reader: BinaryReader, span: dict) -> list[int]:
    return [] if span["offset"] is None else list(reader.bytes_at(span["offset"], span["byteSize"]))


def _read_uint16_array(reader: BinaryReader, span: dict) -> list[int]:
    return [reader.u16(span["offset"] + index * 2) for index in range(span["count"])]


def _read_uint32_array(reader: BinaryReader, span: dict) -> list[int]:
    return [reader.u32(span["offset"] + index * 4) for index in range(span["count"])]


def _read_float_array(reader: BinaryReader, span: dict) -> list[float]:
    return [reader.f32(span["offset"] + index * 4) for index in range(span["count"])]


def _decode_geometry(
    result: dict,
    source: memoryview,
    *,
    unpack_tangents: bool,
) -> None:
    section_data = [
        _decode_section(section, index, source)
        for index, section in enumerate(result["sections"])
    ]
    result["buffers"] = [
        {
            "index": index,
            "type": section["type"],
            "compression": section["compression"],
            "byteLength": len(section_data[index]) if section_data[index] is not None else 0,
            "data": section_data[index],
        }
        for index, section in enumerate(result["sections"])
    ]
    for mesh in result["meshes"]:
        for lod in mesh["lods"]:
            lod["vertex"] = _read_vertex_channels(
                mesh["decl"],
                lod["vb"],
                section_data,
                unpack_tangents=unpack_tangents,
            )
            lod["indices"] = _read_index_groups(mesh, lod, section_data)
            if len(lod["morphTargets"]) > len(mesh["morphTargets"]["targets"]):
                raise CmfError("CMF LOD has more morph buffers than morph target descriptions")
            for index, target in enumerate(lod["morphTargets"]):
                target["vertex"] = _read_vertex_channels(
                    mesh["morphTargets"]["decl"],
                    target["vb"],
                    section_data,
                    unpack_tangents=unpack_tangents,
                )
                description = mesh["morphTargets"]["targets"][index]
                target["name"] = description["name"]
                target["maxDisplacement"] = description["maxDisplacement"]
        if mesh["lods"]:
            mesh["vertex"] = mesh["lods"][0]["vertex"]
            mesh["indices"] = mesh["lods"][0]["indices"]
        else:
            mesh["vertex"] = _empty_vertex()
            mesh["indices"] = []


def _validate_graph_structure(result: dict) -> None:
    sections = result["sections"]

    def validate_decl(decl: list, label: str) -> None:
        keys = {(element["usage"], element["usageIndex"]) for element in decl}
        if len(keys) != len(decl):
            raise CmfError(f"CMF {label} repeats a vertex usage and index")
        for element in decl:
            if not 1 <= element["elementCount"] <= 4:
                raise CmfError(f"CMF {label} has invalid vertex element width")
            element_size = ELEMENT_TYPE_SIZE[element["type"]]
            if element["offset"] % element_size:
                raise CmfError(f"CMF {label} has a misaligned vertex element")
            usage = element["usage"]
            usage_index = element["usageIndex"]
            if usage not in ("PackedTangent", "PackedTangentLegacy"):
                continue
            if any(
                (other, usage_index) in keys
                for other in ("Normal", "Tangent", "Binormal")
            ):
                raise CmfError(
                    f"CMF {label} mixes {usage} with unpacked tangent-space elements"
                )
            expected = (
                ("Int16Norm",)
                if usage == "PackedTangent"
                else ("UInt8Norm", "UInt16Norm")
            )
            if element["type"] not in expected or element["elementCount"] != 4:
                raise CmfError(f"CMF {label} has an invalid {usage} declaration")

    def validate_view(view: dict, label: str, *, index: bool = False) -> None:
        if view["size"] == 0:
            return
        section_index = view["index"]
        if not 0 < section_index < len(sections):
            raise CmfError(f"CMF {label} references invalid section {section_index}")
        section = sections[section_index]
        if section["type"] != "GpuBuffer":
            raise CmfError(f"CMF {label} references a non-GPU section")
        stride = view["stride"]
        if stride == 0 or view["size"] % stride or view["offset"] % stride:
            raise CmfError(f"CMF {label} has an invalid offset, size, or stride")
        if view["offset"] + view["size"] > section["uncompressedSize"]:
            raise CmfError(f"CMF {label} exceeds its GPU section")
        if section["gpuAlignment"] and section["gpuAlignment"] != stride:
            raise CmfError(f"CMF {label} stride disagrees with its GPU section")
        if index and stride not in (2, 4):
            raise CmfError(f"CMF {label} index stride must be 2 or 4")

    for mesh_index, mesh in enumerate(result["meshes"]):
        skeleton = mesh["skeleton"]
        if skeleton is not None and not 0 <= skeleton < len(result["skeletons"]):
            raise CmfError(f"CMF mesh {mesh_index} references missing skeleton {skeleton}")
        validate_decl(mesh["decl"], f"mesh {mesh_index} declaration")
        validate_decl(
            mesh["morphTargets"]["decl"],
            f"mesh {mesh_index} morph declaration",
        )
        for lod_index, lod in enumerate(mesh["lods"]):
            validate_view(lod["vb"], f"mesh {mesh_index} LOD {lod_index} vertex view")
            validate_view(lod["ib"], f"mesh {mesh_index} LOD {lod_index} index view", index=True)
            if lod["ib"]["size"] and lod["ib"]["size"] // lod["ib"]["stride"] % 3:
                raise CmfError(f"CMF mesh {mesh_index} LOD {lod_index} index count is not triangles")
            for morph_index, target in enumerate(lod["morphTargets"]):
                validate_view(
                    target["vb"],
                    f"mesh {mesh_index} LOD {lod_index} morph {morph_index} vertex view",
                )
        if mesh["lods"] and len(mesh["lods"][0]["morphTargets"]) != len(mesh["morphTargets"]["targets"]):
            raise CmfError(f"CMF mesh {mesh_index} morph target descriptions and buffers differ")

    for skeleton_index, skeleton in enumerate(result["skeletons"]):
        count = len(skeleton["bones"])
        if not (
            len(skeleton["parents"])
            == len(skeleton["restTransforms"])
            == len(skeleton["invBindTransforms"])
            == count
        ):
            raise CmfError(f"CMF skeleton {skeleton_index} arrays have different lengths")
        for bone_index, parent in enumerate(skeleton["parents"]):
            if parent != 0xFFFFFFFF and not 0 <= parent < bone_index:
                raise CmfError(
                    f"CMF skeleton {skeleton_index} bone {bone_index} has invalid parent {parent}"
                )

    for animation_index, animation in enumerate(result["animations"]):
        for channel_index, channel in enumerate(animation["channels"]):
            if not 0 <= channel["curveIndex"] < len(animation["curves"]):
                raise CmfError(
                    f"CMF animation {animation_index} channel {channel_index} references missing curve"
                )
        for curve_index, curve in enumerate(animation["curves"]):
            knot_size = ELEMENT_TYPE_SIZE[curve["knotType"]]
            value_size = ELEMENT_TYPE_SIZE[curve["valueType"]]
            if curve["valueDimension"] == 0:
                raise CmfError(f"CMF animation {animation_index} curve {curve_index} has zero dimension")
            if len(curve["knots"]) != curve["knotCount"] * knot_size:
                raise CmfError(f"CMF animation {animation_index} curve {curve_index} knot bytes differ")
            if len(curve["values"]) != curve["knotCount"] * curve["valueDimension"] * value_size:
                raise CmfError(f"CMF animation {animation_index} curve {curve_index} value bytes differ")


def _decode_section(section: dict, index: int, source: memoryview):
    if index == 0 or section["type"] != "GpuBuffer":
        return None
    payload = source[section["offset"] : section["offset"] + section["compressedSize"]]
    compression = section["compression"]
    if compression == "None":
        if section["compressedSize"] != section["uncompressedSize"]:
            raise CmfError("CMF uncompressed GPU section sizes differ")
        return bytes(payload)
    stride = section["gpuAlignment"]
    if stride == 0 or section["uncompressedSize"] % stride:
        raise CmfError("CMF compressed GPU section has invalid alignment")
    count = section["uncompressedSize"] // stride
    if compression == "MeshOptimizerVertexBuffer":
        return decode_vertex_buffer(payload, count, stride)
    if compression == "MeshOptimizerIndexBuffer":
        return decode_index_buffer(payload, count, stride)
    raise CmfError(f"Unsupported CMF section compression {compression!r}")


def _view_data(view: dict, section_data: list):
    if view["size"] == 0:
        return None
    if not 0 <= view["index"] < len(section_data):
        raise CmfError(f"CMF BufferView references missing section {view['index']}")
    section = section_data[view["index"]]
    if section is None:
        raise CmfError(f"CMF BufferView references non-GPU section {view['index']}")
    end = view["offset"] + view["size"]
    if end > len(section):
        raise CmfError("CMF BufferView exceeds its GPU section")
    return memoryview(section)[view["offset"] : end]


def _read_vertex_channels(
    decl: list,
    view: dict,
    section_data: list,
    *,
    unpack_tangents: bool,
) -> dict:
    channels = _empty_vertex()
    data = _view_data(view, section_data)
    if data is None or view["stride"] == 0:
        return channels
    if view["size"] % view["stride"]:
        raise CmfError("CMF vertex BufferView size is not divisible by stride")
    vertex_count = view["size"] // view["stride"]
    for element in decl:
        size = ELEMENT_TYPE_SIZE[element["type"]]
        if element["elementCount"] == 0:
            raise CmfError("CMF vertex element has zero components")
        if element["offset"] + element["elementCount"] * size > view["stride"]:
            raise CmfError("CMF vertex element exceeds vertex stride")
        name = _channel_name(element)
        channels.setdefault(name, [])
        for vertex in range(vertex_count):
            base = vertex * view["stride"] + element["offset"]
            for component in range(element["elementCount"]):
                channels[name].append(_read_element(data, base + component * size, element["type"]))
    if unpack_tangents:
        _unpack_vertex_tangent_channels(channels, decl)
    return channels


def _unpack_vertex_tangent_channels(channels: dict, decl: list) -> None:
    packed_elements = {}
    for element in decl:
        usage = element["usage"]
        if usage not in ("PackedTangent", "PackedTangentLegacy"):
            continue
        usage_index = element["usageIndex"]
        previous = packed_elements.get(usage_index)
        if previous is None or usage == "PackedTangent":
            packed_elements[usage_index] = element
    for usage_index, element in packed_elements.items():
        usage = element["usage"]
        packed_name = _channel_name(element)
        frame = unpack_packed_tangents(channels.get(packed_name) or [], usage)
        suffix = str(usage_index) if usage_index else ""
        channels["normal"] = frame["normal"]
        channels[f"tangent{suffix}"] = frame["tangent"]
        channels[f"binormal{suffix}"] = frame["binormal"]


def _channel_name(element: dict) -> str:
    usage = element["usage"]
    base = CHANNEL_NAMES.get(usage, usage[:1].lower() + usage[1:])
    if usage in ("TexCoord", "Color") or element["usageIndex"] > 0:
        return f"{base}{element['usageIndex']}"
    return base


def _read_element(data: memoryview, offset: int, element_type: str):
    formats = {
        "Float32": "<f",
        "Float16": "<e",
        "UInt16Norm": "<H",
        "UInt16": "<H",
        "Int16Norm": "<h",
        "Int16": "<h",
        "UInt8Norm": "<B",
        "UInt8": "<B",
        "Int8Norm": "<b",
        "Int8": "<b",
    }
    try:
        value = struct.unpack_from(formats[element_type], data, offset)[0]
    except struct.error as error:
        raise CmfError("CMF vertex element exceeds BufferView") from error
    if element_type == "UInt16Norm":
        return value / 65535
    if element_type == "Int16Norm":
        return max(value / 32767, -1)
    if element_type == "UInt8Norm":
        return value / 255
    if element_type == "Int8Norm":
        return max(value / 127, -1)
    return value


def _read_index_groups(mesh: dict, lod: dict, section_data: list) -> list:
    data = _view_data(lod["ib"], section_data)
    stride = lod["ib"]["stride"]
    if data is None or stride == 0:
        return []
    if stride not in (1, 2, 4) or lod["ib"]["size"] % stride:
        raise CmfError("CMF index BufferView has invalid stride or size")
    codes = {1: "<B", 2: "<H", 4: "<I"}
    faces = [
        struct.unpack_from(codes[stride], data, offset)[0]
        for offset in range(0, lod["ib"]["size"], stride)
    ]
    if not lod["areas"]:
        return [{"name": "", "bytesPerIndex": stride, "faces": faces}]
    output = []
    for index, area in enumerate(lod["areas"]):
        start = area["firstElement"] * 3
        end = (area["firstElement"] + area["elementCount"]) * 3
        if end > len(faces):
            raise CmfError("CMF LOD area exceeds index BufferView")
        source_area = mesh["areas"][index] if index < len(mesh["areas"]) else None
        output.append(
            {
                "name": source_area["name"] if source_area else "",
                "bytesPerIndex": stride,
                "firstElement": area["firstElement"],
                "elementCount": area["elementCount"],
                "faces": faces[start:end],
            }
        )
    return output


def _empty_vertex() -> dict:
    return {
        "position": [],
        "normal": [],
        "tangent": [],
        "binormal": [],
        "texcoord0": [],
        "texcoord1": [],
        "color0": [],
        "blendIndice": [],
        "blendWeight": [],
        "packedTangent": [],
        "packedTangentLegacy": [],
    }


__all__ = ["inspect_cmf", "read_cmf"]

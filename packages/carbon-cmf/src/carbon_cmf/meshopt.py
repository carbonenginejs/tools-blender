"""Pure-Python meshoptimizer vertex/index decoders used by CMF.

Ported from ``meshopt_decoder_reference.js`` in meshoptimizer, MIT licensed.
Copyright (C) 2016-2026 Arseny Kapoulkine.
The reference implementation is by Jasper St. Pierre.
"""

from __future__ import annotations

import struct

from .binary import CmfError


def _fail(condition: bool, message: str) -> None:
    if not condition:
        raise CmfError(f"CMF meshoptimizer decode: {message}")


def _dezig(value: int) -> int:
    return (value >> 1) ^ -(value & 1)


def _u32(value: int) -> int:
    return value & 0xFFFFFFFF


def _source_byte(source: memoryview, offset: int) -> int:
    if not 0 <= offset < len(source):
        raise CmfError("CMF meshoptimizer stream ended unexpectedly")
    return source[offset]


def decode_vertex_buffer(source, element_count: int, byte_stride: int) -> bytes:
    """Decode meshoptimizer vertex-buffer versions 0 and 1."""

    source = memoryview(source).cast("B")
    _fail(len(source) > 0 and source[0] in (0xA0, 0xA1), "invalid vertex header")
    _fail(element_count >= 0, "negative vertex count")
    _fail(byte_stride > 0 and byte_stride % 4 == 0, "vertex stride must be positive and divisible by 4")
    version = source[0] & 0x0F
    max_block_elements = min((0x2000 // byte_stride) & ~0x0F, 0x100)
    _fail(max_block_elements > 0, "vertex stride is too large")

    deltas = bytearray(max_block_elements * byte_stride)
    tail_size = byte_stride if version == 0 else byte_stride + byte_stride // 4
    tail_size_padded = max(tail_size, 32 if version == 0 else 24)
    _fail(len(source) >= 1 + tail_size_padded, "vertex stream is too short")
    tail_offset = len(source) - tail_size
    previous = bytearray(source[tail_offset : tail_offset + byte_stride])
    channels = None if version == 0 else source[tail_offset + byte_stride : tail_offset + tail_size]
    target = bytearray(element_count * byte_stride)
    source_offset = 1
    header_modes = ((0, 2, 4, 8), (0, 1, 2, 4), (1, 2, 4, 8))

    for element_base in range(0, element_count, max_block_elements):
        block_count = min(element_count - element_base, max_block_elements)
        group_count = (block_count + 0x0F) >> 4
        header_byte_count = (group_count + 0x03) >> 2
        control_bits_offset = source_offset
        source_offset += 0 if version == 0 else byte_stride // 4
        deltas[:] = b"\0" * len(deltas)

        for byte_index in range(byte_stride):
            delta_base = byte_index * block_count
            control_mode = 0
            if version != 0:
                control = _source_byte(source, control_bits_offset + (byte_index >> 2))
                control_mode = (control >> ((byte_index & 3) << 1)) & 3
            if control_mode == 2:
                continue
            if control_mode == 3:
                end = source_offset + block_count
                _fail(end <= len(source), "truncated raw vertex deltas")
                deltas[delta_base : delta_base + block_count] = source[source_offset:end]
                source_offset = end
                continue

            header_bits_offset = source_offset
            source_offset += header_byte_count
            for group in range(group_count):
                header = _source_byte(source, header_bits_offset + (group >> 2))
                mode = (header >> ((group & 3) << 1)) & 3
                mode_bits = header_modes[0 if version == 0 else control_mode + 1][mode]
                delta_offset = delta_base + (group << 4)
                if mode_bits == 0:
                    continue
                if mode_bits == 1:
                    source_base = source_offset
                    source_offset += 2
                    for item in range(16):
                        delta = (_source_byte(source, source_base + (item >> 3)) >> (item & 7)) & 1
                        if delta == 1:
                            delta = _source_byte(source, source_offset)
                            source_offset += 1
                        if delta_offset + item < delta_base + block_count:
                            deltas[delta_offset + item] = delta
                elif mode_bits == 2:
                    source_base = source_offset
                    source_offset += 4
                    for item in range(16):
                        shift = 6 - ((item & 3) << 1)
                        delta = (_source_byte(source, source_base + (item >> 2)) >> shift) & 3
                        if delta == 3:
                            delta = _source_byte(source, source_offset)
                            source_offset += 1
                        if delta_offset + item < delta_base + block_count:
                            deltas[delta_offset + item] = delta
                elif mode_bits == 4:
                    source_base = source_offset
                    source_offset += 8
                    for item in range(16):
                        shift = 4 - ((item & 1) << 2)
                        delta = (_source_byte(source, source_base + (item >> 1)) >> shift) & 0x0F
                        if delta == 0x0F:
                            delta = _source_byte(source, source_offset)
                            source_offset += 1
                        if delta_offset + item < delta_base + block_count:
                            deltas[delta_offset + item] = delta
                else:
                    end = source_offset + 16
                    _fail(end <= len(source), "truncated verbatim vertex deltas")
                    copy_count = min(16, delta_base + block_count - delta_offset)
                    if copy_count > 0:
                        deltas[delta_offset : delta_offset + copy_count] = source[
                            source_offset : source_offset + copy_count
                        ]
                    source_offset = end

        for element in range(block_count):
            destination_element = element_base + element
            for byte_group in range(0, byte_stride, 4):
                channel_mode = 0 if version == 0 else channels[byte_group >> 2] & 3
                _fail(channel_mode != 3, "reserved vertex channel mode")
                if channel_mode == 0:
                    for byte_index in range(byte_group, byte_group + 4):
                        delta = _dezig(deltas[byte_index * block_count + element])
                        value = (previous[byte_index] + delta) & 0xFF
                        target[destination_element * byte_stride + byte_index] = value
                        previous[byte_index] = value
                elif channel_mode == 1:
                    for byte_index in range(byte_group, byte_group + 4, 2):
                        delta = _dezig(
                            deltas[byte_index * block_count + element]
                            | (deltas[(byte_index + 1) * block_count + element] << 8)
                        )
                        value = (
                            previous[byte_index]
                            | (previous[byte_index + 1] << 8)
                        )
                        value = (value + delta) & 0xFFFF
                        destination = destination_element * byte_stride + byte_index
                        target[destination] = previous[byte_index] = value & 0xFF
                        target[destination + 1] = previous[byte_index + 1] = value >> 8
                else:
                    byte_index = byte_group
                    delta = (
                        deltas[byte_index * block_count + element]
                        | (deltas[(byte_index + 1) * block_count + element] << 8)
                        | (deltas[(byte_index + 2) * block_count + element] << 16)
                        | (deltas[(byte_index + 3) * block_count + element] << 24)
                    )
                    value = (
                        previous[byte_index]
                        | (previous[byte_index + 1] << 8)
                        | (previous[byte_index + 2] << 16)
                        | (previous[byte_index + 3] << 24)
                    )
                    rotation = channels[byte_group >> 2] >> 4
                    rotated = delta if rotation == 0 else _u32(
                        (delta >> rotation) | (delta << (32 - rotation))
                    )
                    value = _u32(value ^ rotated)
                    destination = destination_element * byte_stride + byte_index
                    for component in range(4):
                        byte_value = (value >> (component * 8)) & 0xFF
                        target[destination + component] = byte_value
                        previous[byte_index + component] = byte_value

    _fail(source_offset == len(source) - tail_size_padded, "vertex stream size mismatch")
    return bytes(target)


class _Fifo:
    def __init__(self, size: int):
        self.values = [0] * size
        self.offset = 0

    def read(self, distance: int) -> int:
        return self.values[(self.offset - 1 - distance) & (len(self.values) - 1)]

    def push(self, value: int) -> None:
        self.values[self.offset] = _u32(value)
        self.offset = (self.offset + 1) & (len(self.values) - 1)


def decode_index_buffer(source, count: int, byte_stride: int) -> bytes:
    """Decode a meshoptimizer triangle index buffer."""

    source = memoryview(source).cast("B")
    _fail(len(source) >= 17 and source[0] in (0xE0, 0xE1), "invalid index header")
    _fail(count >= 0 and count % 3 == 0, "index count must be a non-negative multiple of 3")
    _fail(byte_stride in (2, 4), "index stride must be 2 or 4")
    triangle_count = count // 3
    version = source[0] & 0x0F
    fec_max = 13 if version >= 1 else 15
    code_offset = 1
    data_offset = code_offset + triangle_count
    code_aux_offset = len(source) - 16
    _fail(data_offset <= code_aux_offset, "index stream is too short")
    next_index = 0
    last = 0
    edges = _Fifo(32)
    vertices = _Fifo(16)
    decoded: list[int] = []

    def read_leb128() -> int:
        nonlocal data_offset
        value = 0
        shift = 0
        while True:
            _fail(data_offset < code_aux_offset, "truncated index LEB128")
            byte = source[data_offset]
            data_offset += 1
            value |= (byte & 0x7F) << shift
            if byte < 0x80:
                return value
            shift += 7
            _fail(shift < 35, "index LEB128 is too large")

    def decode_index(value: int) -> int:
        nonlocal last
        last += _dezig(value)
        return last

    for _ in range(triangle_count):
        _fail(code_offset < data_offset, "truncated triangle codes")
        code = source[code_offset]
        code_offset += 1
        first_code = code >> 4
        second_code = code & 0x0F
        if first_code < 0x0F:
            a = edges.read(first_code * 2)
            b = edges.read(first_code * 2 + 1)
            if second_code < fec_max:
                if second_code == 0:
                    c = next_index
                    next_index += 1
                    vertices.push(c)
                else:
                    c = vertices.read(second_code)
            else:
                if second_code == 0x0D:
                    last -= 1
                    c = last
                elif second_code == 0x0E:
                    last += 1
                    c = last
                else:
                    c = decode_index(read_leb128())
                vertices.push(c)
            edges.push(b)
            edges.push(c)
            edges.push(c)
            edges.push(a)
        else:
            if second_code < 0x0E:
                entry = source[code_aux_offset + second_code]
                z = entry >> 4
                w = entry & 0x0F
                a = next_index
                next_index += 1
                if z == 0:
                    b = next_index
                    next_index += 1
                else:
                    b = vertices.read(z - 1)
                if w == 0:
                    c = next_index
                    next_index += 1
                else:
                    c = vertices.read(w - 1)
                vertices.push(a)
                if z == 0:
                    vertices.push(b)
                if w == 0:
                    vertices.push(c)
            else:
                _fail(data_offset < code_aux_offset, "truncated index code")
                entry = source[data_offset]
                data_offset += 1
                if entry == 0:
                    next_index = 0
                z = entry >> 4
                w = entry & 0x0F
                if second_code == 0x0E:
                    a = next_index
                    next_index += 1
                else:
                    a = decode_index(read_leb128())
                if z == 0:
                    b = next_index
                    next_index += 1
                elif z == 0x0F:
                    b = decode_index(read_leb128())
                else:
                    b = vertices.read(z - 1)
                if w == 0:
                    c = next_index
                    next_index += 1
                elif w == 0x0F:
                    c = decode_index(read_leb128())
                else:
                    c = vertices.read(w - 1)
                vertices.push(a)
                if z in (0, 0x0F):
                    vertices.push(b)
                if w in (0, 0x0F):
                    vertices.push(c)
            edges.push(a)
            edges.push(b)
            edges.push(b)
            edges.push(c)
            edges.push(c)
            edges.push(a)
        decoded.extend((_u32(a), _u32(b), _u32(c)))

    _fail(code_offset == 1 + triangle_count, "index code size mismatch")
    _fail(data_offset == code_aux_offset, "index stream size mismatch")
    if byte_stride == 2:
        _fail(all(value <= 0xFFFF for value in decoded), "decoded index exceeds UInt16")
        return struct.pack(f"<{len(decoded)}H", *decoded)
    return struct.pack(f"<{len(decoded)}I", *decoded)


__all__ = ["decode_index_buffer", "decode_vertex_buffer"]

"""Writing a PNG without Blender.

`Image.save` is Blender's, and Blender's data may only be touched from the main
thread -- which is exactly the thread a nine-second decode must stay off. So
the decoded pixels are written here instead, with zlib and a few headers, and
Blender is handed a file it can open in milliseconds.

Only what is needed: 8-bit RGBA, no interlacing, no palette, filter 0 on every
scanline. That is a legal PNG and every reader takes it.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


SIGNATURE = b"\x89PNG\r\n\x1a\n"


def chunk(kind: bytes, payload: bytes) -> bytes:
    """One PNG chunk: length, type, payload, CRC over type and payload."""

    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def encode(width: int, height: int, rgba) -> bytes:
    """RGBA bytes, top row first, as a complete PNG.

    Top row first because that is PNG's own order and the decoder's. Blender's
    pixel buffer is the other way up, which is why the in-memory path flips and
    this one does not -- getting that backwards produces an upside-down hull
    that looks like a UV bug.
    """

    stride = width * 4
    raw = bytearray()
    for row in range(height):
        raw.append(0)                  # filter type 0: none
        raw += rgba[row * stride:(row + 1) * stride]

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (SIGNATURE
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


def write(path, width: int, height: int, rgba) -> Path:
    """Writes one PNG, via a temporary name so a kill cannot leave half of it.

    A half-written PNG is worse than none: it is a file that exists, so the
    decode is never run again, and every load afterwards shows a broken image.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    partial.write_bytes(encode(width, height, rgba))
    partial.replace(path)
    return path

"""Placeholder art for the slots a real ship fills from outside.

A banner's image is an EXTERNAL parameter: which logo belongs on it depends on
who owns the ship, so a preview has nothing to put there. An empty slot renders
as nothing at all, which reads as a missing feature rather than as a slot
waiting to be filled.

So the slots that a person recognises -- the alliance and corporation logos --
get a labelled placeholder: a dashed square with its name in the middle. The
other twenty-two usages stay black, which is to say invisible, because inventing
art for a recruitment panel would be inventing data.

BLACK MEANS TRANSPARENT. The images are drawn on black and the material takes
its alpha from luminance, so a placeholder is a bright outline floating where
the banner is rather than a black rectangle stuck to the hull.

Images are generated into the blend rather than written to disk: there is no
file to go stale, no path to resolve, and nothing to convert.
"""

from __future__ import annotations

import bpy


#: A 5x7 bitmap font, in the letters the labels need and nothing more.
#:
#: Hand-drawn rather than loaded: Blender ships no font rasteriser reachable
#: from a background build, and nine glyphs is less machinery than making one
#: available would be.
GLYPHS = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "N": ("10001", "11001", "10101", "10101", "10011", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
}

GLYPH_WIDTH = 5
GLYPH_HEIGHT = 7

#: Which banner usages get a labelled placeholder, and what it says.
#:
#: Everything else stays black. See `BANNER_REFERENCES` in `ship` for the whole
#: enum -- there are twenty-four.
BANNER_LABELS = {
    "alliance_logo": "ALLIANCE",
    "corp_logo": "CORP",
}


def _plot(pixels, size, x, y, colour):
    """Sets one pixel, with the origin at the TOP left as a reader expects."""

    if not (0 <= x < size and 0 <= y < size):
        return
    offset = ((size - 1 - y) * size + x) * 4
    pixels[offset:offset + 4] = colour


def _draw_text(pixels, size, text, scale, colour):
    """Draws a line of text centred in the image."""

    text = text.upper()
    width = len(text) * (GLYPH_WIDTH + 1) * scale
    start_x = (size - width) // 2
    start_y = (size - GLYPH_HEIGHT * scale) // 2
    for index, character in enumerate(text):
        glyph = GLYPHS.get(character)
        if glyph is None:
            continue
        origin = start_x + index * (GLYPH_WIDTH + 1) * scale
        for row, bits in enumerate(glyph):
            for column, bit in enumerate(bits):
                if bit != "1":
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        _plot(pixels, size, origin + column * scale + dx,
                              start_y + row * scale + dy, colour)


def _draw_dashed_border(pixels, size, colour, inset=8, dash=10, gap=8, thickness=2):
    """A dashed square, so a placeholder never reads as finished art."""

    step = dash + gap
    for offset in range(inset, size - inset):
        if (offset - inset) % step >= dash:
            continue
        for layer in range(thickness):
            _plot(pixels, size, offset, inset + layer, colour)
            _plot(pixels, size, offset, size - 1 - inset - layer, colour)
            _plot(pixels, size, inset + layer, offset, colour)
            _plot(pixels, size, size - 1 - inset - layer, offset, colour)


def banner_placeholder(slot, size=256):
    """A labelled placeholder for one banner slot, or None when it has no label.

    Returns an existing image when one has already been made, so a scene of
    twenty ships holds one image per slot rather than twenty.
    """

    label = BANNER_LABELS.get(slot)
    if label is None:
        return None

    name = f"carbon_placeholder_{slot}"
    existing = bpy.data.images.get(name)
    if existing is not None:
        return existing

    image = bpy.data.images.new(name, width=size, height=size, alpha=True)
    image.colorspace_settings.name = "Non-Color"
    pixels = [0.0] * (size * size * 4)
    white = [1.0, 1.0, 1.0, 1.0]
    _draw_dashed_border(pixels, size, white)
    _draw_text(pixels, size, label, max(1, size // 64), white)
    image.pixels.foreach_set(pixels)
    image.pack()
    return image

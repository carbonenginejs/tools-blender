"""DDS textures Blender cannot read on its own.

Blender loads DXT1, DXT5, BC4 (ATI1) and BC5 (ATI2) natively. It does NOT load
BC7, which arrives as a 0x0 image with no data -- and BC7 is what EVE's albedo
maps are, 171 of the 445 textures in one cache.

So only BC7 is decoded here. Everything else is handed to Blender untouched.

The decoder is a port of `runtime/src/resource/formats/dds/core/bc7.js`, and
its tables are generated from that file rather than transcribed.
"""

from .reader import (DdsError, decode_bc7, header_of, is_bc7, load_image,
                     to_rgba)

__all__ = ["DdsError", "decode_bc7", "header_of", "is_bc7", "load_image",
           "to_rgba"]

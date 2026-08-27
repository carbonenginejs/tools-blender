"""Real logos for a ship's banners, from CCP's image server.

A banner's image is an EXTERNAL parameter: which logo belongs on it depends on
who owns the ship. Given an alliance or corporation, the picture comes from
`images.evetech.net`.

The image server's rules:

- the image server takes only certain SIZES and answers 400 to anything else,
  so the size is clamped rather than passed through;
- everything it serves is a PNG except character portraits, which are JPEG;
- its URLs have NO extension -- `/alliances/{id}/logo` -- and a consumer that
  picks its decoder off the extension cannot load that. Blender is such a
  consumer, so the file is saved with one.

Fetched images are cached on disk by id and size. A logo changes when an
alliance changes it, which is rare enough that a cache is right and an expiry
is not worth the machinery.
"""

from __future__ import annotations

import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import bpy


#: What the image server accepts. Anything else is a 400.
IMAGE_SIZES = (32, 64, 128, 256, 512, 1024)

#: Which categories carry a logo, and what the variant is called.
LOGO_VARIANTS = {
    "alliance": ("alliances", "logo"),
    "corporation": ("corporations", "logo"),
    "character": ("characters", "portrait"),
}

#: Which banner USAGE takes which owner. Usage is the type of banner, and only
#: three of the twenty-four name an owner the image server can answer for.
USAGE_OWNERS = {
    "alliance_logo": "alliance",
    "corp_logo": "corporation",
    "ceo_portrait": "character",
}

IMAGE_SERVER = "https://images.evetech.net"


class LogoError(RuntimeError):
    """Raised when a logo cannot be fetched."""


def logo_url(kind, identity, size=256):
    """The image-server URL for one owner."""

    if kind not in LOGO_VARIANTS:
        raise LogoError(f"No logo for {kind!r}")
    category, variant = LOGO_VARIANTS[kind]
    size = size if size in IMAGE_SIZES else 256
    return f"{IMAGE_SERVER}/{category}/{int(identity)}/{variant}?size={size}"


def fetch_logo(kind, identity, cache_directory, size=256, timeout=30.0):
    """Downloads one logo, or returns the cached copy.

    The saved name ends in `.png` even for a character portrait, which is
    really a JPEG: Blender picks its loader from the file's CONTENT, but its
    image list and any later reload read better with an extension, and the
    engine-side consumers pick by extension outright.
    """

    size = size if size in IMAGE_SIZES else 256
    os.makedirs(cache_directory, exist_ok=True)
    local = os.path.join(cache_directory, f"{kind}_{int(identity)}_{size}.png")
    if os.path.exists(local) and os.path.getsize(local) > 0:
        return local

    request = Request(logo_url(kind, identity, size),
                      headers={"Accept": "image/png", "User-Agent": "carbonenginejs-tools-blender"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except (HTTPError, URLError, OSError) as error:
        raise LogoError(f"Could not fetch the {kind} logo for {identity}: {error}") from error

    if not payload:
        raise LogoError(f"The image server returned nothing for {kind} {identity}")
    with open(local, "wb") as handle:
        handle.write(payload)
    return local


def banner_logo(usage, owners, cache_directory, size=256):
    """The image for one banner TYPE, or None when there is nobody to ask about.

    `owners` maps `alliance`, `corporation` and `character` to ids. A usage whose
    owner is absent returns None -- a missing owner is not an error, it is a ship
    nobody has claimed.
    """

    kind = USAGE_OWNERS.get(usage)
    identity = (owners or {}).get(kind) if kind else None
    if not identity:
        return None

    name = f"carbon_logo_{kind}_{int(identity)}"
    existing = bpy.data.images.get(name)
    if existing is not None:
        return existing

    local = fetch_logo(kind, identity, cache_directory, size)
    image = bpy.data.images.load(local, check_existing=True)
    image.name = name
    image.colorspace_settings.name = "sRGB"
    image.pack()
    return image

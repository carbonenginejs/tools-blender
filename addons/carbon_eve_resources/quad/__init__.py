"""Quad-family shader support: the measured interface, and the material maths.

Neither module imports ``bpy``. The Blender node emitter is built on top of
them; the arithmetic and the interface data stay testable on their own.
"""

from .interface import (
    Annotation,
    Constant,
    Family,
    Member,
    QuadInterfaceError,
    load_family,
    normalize_shader_name,
)
from . import reference

__all__ = [
    "Annotation",
    "Constant",
    "Family",
    "Member",
    "QuadInterfaceError",
    "load_family",
    "normalize_shader_name",
    "reference",
]

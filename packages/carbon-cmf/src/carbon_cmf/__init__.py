"""Standalone, stdlib-only CMF v1 read library."""

from .binary import CmfError
from .constants import FILE_SIGNATURE, FILE_VERSION
from .graph import build_cmf_from_shared, build_shared_from_cmf
from .reader import inspect_cmf, read_cmf
from .tangents import (
    PACKED_TANGENT,
    PACKED_TANGENT_LEGACY,
    decode_packed_tangent,
    unpack_packed_tangents,
)

__version__ = "0.1.0"
inspect = inspect_cmf

__all__ = [
    "CmfError",
    "FILE_SIGNATURE",
    "FILE_VERSION",
    "PACKED_TANGENT",
    "PACKED_TANGENT_LEGACY",
    "build_cmf_from_shared",
    "build_shared_from_cmf",
    "decode_packed_tangent",
    "__version__",
    "inspect",
    "inspect_cmf",
    "read_cmf",
    "unpack_packed_tangents",
]

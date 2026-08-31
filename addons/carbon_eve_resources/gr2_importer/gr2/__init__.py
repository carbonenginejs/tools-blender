"""Compatibility facade for the extracted GR2 and GSF packages."""

from carbon_gr2 import Gr2Error, RawGr2, decode_curve, inspect, read_gr2, read_raw, sample_curve
from carbon_gsf import GsfError, is_gsf, read_gsf as _read_gsf


def read_gsf(source):
    """Legacy facade preserving the former GR2-family exception type."""

    try:
        return _read_gsf(source)
    except GsfError as error:
        raise Gr2Error(str(error)) from error


__all__ = [
    "Gr2Error",
    "RawGr2",
    "decode_curve",
    "inspect",
    "is_gsf",
    "read_gr2",
    "read_gsf",
    "read_raw",
    "sample_curve",
]

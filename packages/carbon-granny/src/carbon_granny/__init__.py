"""Shared pure-Python Granny container and reflection reader."""

from .reader import MEMBER_TYPES, RawGr2, TypedList, read_raw

__version__ = "0.1.0"


def is_gstate_root(file_info) -> bool:
    """Return whether a reflected Granny root uses the GState schema."""

    root = file_info or {}
    return root.get("StateMachine") is not None and isinstance(root.get("AnimationSets"), list)


__all__ = ["MEMBER_TYPES", "RawGr2", "TypedList", "__version__", "is_gstate_root", "read_raw"]

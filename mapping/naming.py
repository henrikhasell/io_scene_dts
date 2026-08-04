"""Torque naming conventions: detail-size suffixes on object names.

Torque's exporters encoded the detail level in a trailing number on the object
name: "shape2", "shape32" are visible details of size 2/32; "collision-1" and
"loscollision-1" are collision details (negative size).  The trailing number is
authoritative on export; the base name is the DTS object name.
"""

from __future__ import annotations

import re

_SUFFIX_RE = re.compile(r"^(.*?)(-?\d+)$")


def split_detail_suffix(name: str) -> tuple[str, int | None]:
    """"shape2" -> ("shape", 2); "collision-1" -> ("collision", -1);
    "eye" -> ("eye", None)."""
    m = _SUFFIX_RE.match(name)
    if not m or not m.group(1):
        return name, None
    return m.group(1), int(m.group(2))


def object_display_name(base: str, detail_size: int) -> str:
    return f"{base}{detail_size}"


def detail_name_for_size(size: int) -> str:
    """The conventional detail-marker name for a detail of a given size."""
    if size < 0:
        return f"Collision-{-size}" if size != -1 else "Collision-1"
    return f"detail{size}"


def strip_blender_dedup(name: str) -> str:
    """Remove Blender's ".001"-style duplicate suffix."""
    return re.sub(r"\.\d{3,}$", "", name)

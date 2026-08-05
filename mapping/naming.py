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


def dts_object_and_size(bobj) -> tuple[str, int]:
    """The DTS object name and detail size a mesh object belongs to.

    Imported meshes carry both as properties; a mesh made in Blender says so
    with its name instead ("hull32" is the hull object at detail 32), which is
    Torque's own convention and the only thing a user has to know.

    Shared so the exporter's grouping and the decal authoring path cannot
    disagree about which object a mesh is part of -- they did, and a decal
    built on a fresh mesh was attributed to an object with no name.
    """
    base = bobj.get("dts_object_name") or None
    size = bobj.get("dts_detail_size")
    if base is None or size is None:
        parsed_base, parsed_size = split_detail_suffix(strip_blender_dedup(bobj.name))
        base = base or parsed_base
        size = size if size is not None else (parsed_size if parsed_size is not None else 2)
    return str(base), int(size)

"""Trailing material list IO.

Port of MaterialList::read/write (dgl/materialList.cc) and
TSMaterialList::read/write (ts/tsMaterialList.cc).

Names are preserved exactly as stored (including any legacy path prefix)
rather than normalised; Material.basename gives the engine's view.
"""

from __future__ import annotations

from .errors import DtsError
from .stream import StreamReader, StreamWriter
from .types import (
    MAT_NEVER_ENV_MAP,
    MAT_S_WRAP,
    MAT_T_WRAP,
    MAT_TRANSLUCENT,
    NO_MAP,
    Material,
)

BINARY_FILE_VERSION = 1


def read_material_list(r: StreamReader, version: int) -> list[Material]:
    """version is the enclosing shape's DTS version (drives field presence)."""
    file_version = r.u8()
    if file_version != BINARY_FILE_VERSION:
        # The engine falls back to a newline-separated text list here; no
        # corpus file does this inside a .dts, so refuse loudly instead.
        raise DtsError(f"material list version {file_version}, expected {BINARY_FILE_VERSION}")

    count = r.u32()
    mats = [Material(name=r.pascal_string()) for _ in range(count)]

    if version < 2:
        for m in mats:
            m.flags = MAT_S_WRAP | MAT_T_WRAP
    else:
        for m in mats:
            m.flags = r.u32()

    if version < 5:
        for i, m in enumerate(mats):
            m.reflectance_map = i
            m.bump_map = NO_MAP
            m.detail_map = NO_MAP
    else:
        for m in mats:
            m.reflectance_map = r.u32()
        for m in mats:
            m.bump_map = r.u32()
        for m in mats:
            m.detail_map = r.u32()

    if version > 11:
        for m in mats:
            m.detail_scale = r.f32()
    else:
        for m in mats:
            m.detail_scale = 1.0

    if version > 20:
        for m in mats:
            m.reflection_amount = r.f32()
    else:
        for m in mats:
            m.reflection_amount = 1.0

    if version < 16:
        for m in mats:
            if m.flags & MAT_TRANSLUCENT:
                m.flags |= MAT_NEVER_ENV_MAP

    return mats


def write_material_list(w: StreamWriter, mats: list[Material]) -> None:
    """Always the modern (v21+) full layout — correct for v23/v24 writes."""
    w.u8(BINARY_FILE_VERSION)
    w.u32(len(mats))
    for m in mats:
        w.pascal_string(m.name)
    for m in mats:
        w.u32(m.flags)
    for m in mats:
        w.u32(m.reflectance_map)
    for m in mats:
        w.u32(m.bump_map)
    for m in mats:
        w.u32(m.detail_map)
    for m in mats:
        w.f32(m.detail_scale)
    for m in mats:
        w.f32(m.reflection_amount)

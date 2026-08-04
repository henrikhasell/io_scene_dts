"""Reader for pre-v19 DTS shapes (versions 17-18).

Port of TSShape::readOldShape (tsShapeOldRead.cc:390): the old format is one
flat stream; the engine converts it into the three-buffer memory image and
then runs the ordinary assembler over it.  We do the same — build the buffers
with WriteAlloc (whose guard() matches the engine's DebugGuard) and feed
reader._assemble_shape, so all the v<19 assembly branches are shared.

Versions below 17 (the keyframe-table era) are refused: they need the
obsolete Keyframe vector and rearrangeKeyframeData, and only a handful of
files that old exist.
"""

from __future__ import annotations

import struct

from .errors import DtsError, DtsUnsupportedVersion
from .matlist import read_material_list
from .primitives import bits_to_f32, f32_to_bits
from .sequence_io import read_sequence
from .stream import ReadAlloc, StreamReader, WriteAlloc
from .types import (
    DECAL_MESH,
    MAT_MIP_MAP_ZERO_BORDER,
    MAT_S_WRAP,
    MAT_T_WRAP,
    MAT_TRANSLUCENT,
    NULL_MESH,
    SKIN_MESH,
    SORTED_MESH,
    Shape,
)

OLD_MIN_VERSION = 17


def read_old_shape(data: bytes, version: int, exporter_version: int) -> Shape:
    if version < OLD_MIN_VERSION:
        raise DtsUnsupportedVersion(version)
    try:
        return _read_old_shape(data, version, exporter_version)
    except struct.error as e:
        raise DtsError(f"truncated or corrupt old-format DTS file: {e}") from e


def _read_old_shape(data: bytes, version: int, exporter_version: int) -> Shape:
    r = StreamReader(data, pos=4)  # version word already consumed
    w = WriteAlloc()
    counts = [0] * 15  # patched into the buffer head at the end

    def stored32(idx: int | None = None) -> int:
        v = r.s32()
        w.set32(v)
        if idx is not None:
            counts[idx] = v
        return v

    def counted32(idx: int) -> int:
        """A count that lives in the header block, not inline."""
        v = r.s32()
        counts[idx] = v
        return v

    w.set32n(counts)  # 15-word header placeholder at offset 0
    w.guard()

    # bounds block: radius, tubeRadius, center, bounds — raw F32 copies
    w.b32 += r.raw(4 * 11)
    w.guard()

    num_nodes = counted32(0)
    for _ in range(num_nodes):
        w.b32 += r.raw(8)  # nameIndex, parentIndex
        if version < 17:
            r.bool8()  # obsolete member
        w.set32n((-1, -1, -1))  # runtime slots
    w.guard()

    num_objects = counted32(1)
    for _ in range(num_objects):
        w.b32 += r.raw(16)
        w.set32n((-1, -1))
    w.guard()

    num_decals = counted32(2)
    for _ in range(num_decals):
        w.b32 += r.raw(16)
        w.set32(-1)
    w.guard()

    num_ifl = counted32(4)
    for _ in range(num_ifl):
        w.b32 += r.raw(8)
        w.set32n((0, 0, 0))
    w.guard()

    num_sub_shapes = counted32(3)
    first_node = [r.s32() for _ in range(num_sub_shapes)]
    r.s32()  # toss
    first_object = [r.s32() for _ in range(num_sub_shapes)]
    r.s32()  # toss
    first_decal = [r.s32() for _ in range(num_sub_shapes)]
    w.set32n(first_node + first_object + first_decal)
    w.guard()

    # subShapeNum* computed from the firsts
    for firsts, total in ((first_node, num_nodes), (first_object, num_objects), (first_decal, num_decals)):
        prev = total
        nums = [0] * num_sub_shapes
        for j in range(num_sub_shapes - 1, -1, -1):
            nums[j] = prev - firsts[j]
            prev = firsts[j]
        w.set32n(nums)
    w.guard()

    # node states: interleaved Quat16 + Point3F (defaults are the first
    # numNodes entries; the split happens in the assembler)
    num_node_states = counted32(5)
    for _ in range(num_node_states):
        w.b16 += r.raw(8)
        w.b32 += r.raw(12)
    w.guard()

    num_object_states = counted32(6)
    w.b32 += r.raw(12 * num_object_states)
    w.guard()

    num_decal_states = counted32(7)
    w.b32 += r.raw(4 * num_decal_states)
    w.guard()

    num_triggers = counted32(8)
    w.b32 += r.raw(8 * num_triggers)
    w.guard()

    num_details = counted32(9)
    counts[13] = 0  # smallestVisibleSize
    counts[14] = 0  # smallestVisibleDL
    for i in range(num_details):
        head = r.raw(16)  # nameIndex, subShapeNum, objectDetailNum, size
        w.b32 += head
        w.set32(f32_to_bits(-1.0))  # averageError
        w.set32(f32_to_bits(-1.0))  # maxError
        w.set32(0)  # polyCount
        dsize = int(bits_to_f32(struct.unpack_from("<i", head, 12)[0]))
        if dsize >= 0:
            counts[13] = dsize
            counts[14] = i
    w.guard()

    # sequences, mid-stream
    num_sequences = r.s32()
    sequences = [read_sequence(r, version, read_name_index=True) for _ in range(num_sequences)]

    # meshes: type word + payload straight from the stream
    num_meshes = counted32(10)
    for _ in range(num_meshes):
        mesh_type = r.s32()
        w.set32(mesh_type)
        _read_old_mesh(r, w, version, mesh_type & 0x7)
    w.guard()

    # names: S32-length strings, NUL-terminated into the 8-bit buffer
    num_names = counted32(12)
    for _ in range(num_names):
        sz = r.s32()
        w.b8 += r.raw(sz)
        w.set8(0)
    w.guard()

    # material list, mid-stream
    got_list = r.s32()
    materials = read_material_list(r, version) if got_list else []
    if exporter_version < 116:
        # translucent non-tiling materials get the zero-border property
        for m in materials:
            if (m.flags & MAT_TRANSLUCENT) and not (m.flags & (MAT_S_WRAP | MAT_T_WRAP)):
                m.flags |= MAT_MIP_MAP_ZERO_BORDER

    # reserved detailFirstSkin / detailNumSkins, patched after the skins
    skin_counts_offset = len(w.b32)
    w.set32n([0] * (2 * num_details))
    w.guard()

    num_skins = counted32(11)
    for _ in range(num_skins):
        _read_old_mesh(r, w, version, SKIN_MESH)
    w.guard()

    if num_skins:
        r.s32()  # count word, tossed
        firsts = [r.s32() for _ in range(num_details)]
        prev = num_skins
        nums = [0] * num_details
        for i in range(num_details - 1, -1, -1):
            nums[i] = prev - firsts[i]
            prev = firsts[i]
        struct.pack_into(f"<{2 * num_details}i", w.b32, skin_counts_offset, *(firsts + nums))
    w.guard()

    # patch the counts header
    struct.pack_into("<15i", w.b32, 0, *counts)

    # hand the buffers to the ordinary assembler
    from .reader import _assemble_shape

    shape = Shape(source_version=version, exporter_version=exporter_version)
    shape.sequences = sequences
    shape.materials = materials
    alloc = ReadAlloc(bytes(w.b32), bytes(w.b16), bytes(w.b8))
    _assemble_shape(shape, alloc, version)

    # old files carry no mesh bounds — the engine recomputes them on load
    for mesh in shape.meshes:
        if mesh is not None and mesh.mesh_type != DECAL_MESH:
            _compute_mesh_bounds(mesh)
    return shape


def _read_old_mesh(r: StreamReader, w: WriteAlloc, version: int, mesh_type: int) -> None:
    """Port of readAllocMesh (tsShapeOldRead.cc:192)."""
    if mesh_type == NULL_MESH:
        return

    w.guard()
    w.b32 += r.raw(8)  # numFrames, numMatFrames
    w.set32(-1)  # parentMesh
    w.set32n([0] * 10)  # bounds/center/radius filler (recomputed on load)

    num_verts = r.s32()
    w.set32(num_verts)
    w.b32 += r.raw(12 * num_verts)

    num_tverts = r.s32()
    w.set32(num_tverts)
    w.b32 += r.raw(8 * num_tverts)

    num_norms = r.s32()  # count present in stream but not in the buffer
    w.b32 += r.raw(12 * num_norms)

    num_prims = r.s32()
    w.set32(num_prims)
    if version < 18:
        for _ in range(num_prims):
            a = r.s32()
            b = r.s32()
            w.set16n((_s16(a), _s16(b)))
            w.set32(r.s32())  # matIndex
    else:
        for _ in range(num_prims):
            w.b16 += r.raw(4)
            w.b32 += r.raw(4)

    num_indices = r.s32()
    w.set32(num_indices)
    if version < 18:
        for _ in range(num_indices):
            w.setu16n((r.u32() & 0xFFFF,))
    else:
        w.b16 += r.raw(2 * num_indices)

    w.set32(0)  # mergeIndices: none
    w.b32 += r.raw(8)  # vertsPerFrame, flags
    w.guard()

    if mesh_type == SKIN_MESH:
        sz = r.s32()
        w.set32(sz)
        w.b32 += r.raw(12 * sz)  # initial verts
        sz2 = r.s32()
        w.b32 += r.raw(12 * sz2)  # initial norms (count assumed == verts)
        sz = r.s32()
        w.set32(sz)
        w.b32 += r.raw(64 * sz)  # initial transforms
        nvi = r.s32()
        w.set32(nvi)
        w.b32 += r.raw(4 * nvi)  # vertexIndex
        r.s32()  # boneIndex count, tossed (same as nvi)
        w.b32 += r.raw(4 * nvi)  # boneIndex
        weight_offset = len(w.b32)
        w.set32n([0] * nvi)  # weights, patched below (they follow nodeIndex in the stream)
        nni = r.s32()
        w.set32(nni)
        w.b32 += r.raw(4 * nni)  # nodeIndex
        r.s32()  # weight count, tossed
        w.b32[weight_offset : weight_offset + 4 * nvi] = r.raw(4 * nvi)
        w.guard()
    elif mesh_type == SORTED_MESH:
        sz = r.s32()
        w.set32(sz)
        w.b32 += r.raw(32 * sz)  # clusters
        for _ in range(4):  # startCluster, firstVerts, numVerts, firstTVerts
            sz = r.s32()
            w.set32(sz)
            w.b32 += r.raw(4 * sz)
        w.set32(1 if r.bool8() else 0)  # alwaysWriteDepth
        w.guard()
    elif mesh_type == DECAL_MESH:
        sz = r.s32()
        w.set32(sz)
        w.b32 += r.raw(4 * sz)  # startPrimitive
        if version >= 17:
            for _ in range(r.s32()):  # obsolete startTVerts
                r.s32()
            for _ in range(r.s32()):  # obsolete tvertIndex
                r.s32()
        w.set32(r.s32())  # materialIndex
        w.guard()


def _s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v


def _compute_mesh_bounds(mesh) -> None:
    """Port of TSMesh::computeBounds for old shapes (stored bounds untrusted)."""
    verts = mesh.verts or mesh.initial_verts
    if not verts:
        return
    xs, ys, zs = zip(*verts)
    mn = (min(xs), min(ys), min(zs))
    mx = (max(xs), max(ys), max(zs))
    mesh.bounds = mn + mx
    # keep the center float32-representable so a rewrite round-trips exactly
    center = tuple(bits_to_f32(f32_to_bits((a + b) / 2.0)) for a, b in zip(mn, mx))
    mesh.center = center
    radius = max(
        ((v[0] - center[0]) ** 2 + (v[1] - center[1]) ** 2 + (v[2] - center[2]) ** 2) ** 0.5
        for v in verts
    )
    mesh.radius_int = int(radius)

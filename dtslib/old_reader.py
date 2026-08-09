"""Reader for pre-v19 DTS shapes (versions 15-18).

Port of TSShape::readOldShape (tsShapeOldRead.cc:390): the old format is one
flat stream; the engine converts it into the three-buffer memory image and
then runs the ordinary assembler over it.  We do the same — build the buffers
with WriteAlloc (whose guard() matches the engine's DebugGuard) and feed
reader._assemble_shape, so all the v<19 assembly branches are shared.

Two eras live in here:

- v17-18: node/object/decal animation state is already stored per channel, the
  way every later version stores it.
- v15-16: state is stored per *keyframe* instead, indexed through an obsolete
  Keyframe table.  ``_rearrange_keyframe_data`` transposes it into the modern
  layout and lifts each sequence's base indices out of that table, exactly as
  TSShape::rearrangeKeyframeData does (:697).  v15 additionally keeps a
  meshIndexList instead of null-mesh type words.

Versions below 15 are refused: no file that old exists in any corpus here, and
the branches they need (default decal states pre-14, missing trigger and
tool-begin fields, ifl materials identified by file extension) would be
written blind.
"""

from __future__ import annotations

import struct

from .errors import DtsError, DtsUnsupportedVersion
from .matlist import read_material_list
from .mesh_io import compute_mesh_bounds
from .primitives import bits_to_f32, f32_to_bits
from .sequence_io import object_membership, read_sequence
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

OLD_MIN_VERSION = 15


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

    if version < 16:
        # mesh presence list: one entry per object mesh slot, -1 where a slot
        # has no mesh (what a NullMesh type word says in v16+)
        mesh_index_list_size = stored32()
        w.b32 += r.raw(4 * mesh_index_list_size)

    keyframes = []
    if version < 17:
        # obsolete Keyframe table: read whole, kept out of the buffer.  Only
        # the first entry of each sequence's range is ever used
        for _ in range(r.s32()):
            keyframes.append((r.s32(), r.s32(), r.s32()))

    # node states: interleaved Quat16 + Point3F (defaults are the first
    # numNodes entries; the split happens in the assembler)
    num_node_states = counted32(5)
    node_state_start32 = len(w.b32)
    node_state_start16 = len(w.b16)
    for _ in range(num_node_states):
        w.b16 += r.raw(8)
        w.b32 += r.raw(12)
    w.guard()

    num_object_states = counted32(6)
    object_state_start = len(w.b32)
    w.b32 += r.raw(12 * num_object_states)
    w.guard()

    num_decal_states = counted32(7)
    decal_state_start = len(w.b32)
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
    kf_starts: list[int] = []
    sequences = [
        read_sequence(r, version, read_name_index=True, kf_starts=kf_starts)
        for _ in range(num_sequences)
    ]

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

    if version < 17:
        for seq, kf_start in zip(sequences, kf_starts):
            _rearrange_keyframe_data(
                seq,
                keyframes,
                kf_start,
                w,
                node_state_start32,
                node_state_start16,
                object_state_start,
                decal_state_start,
            )

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
            compute_mesh_bounds(mesh)
    return shape


def _rearrange_states(buf: bytearray, region: int, start: int, a: int, b: int, size: int) -> None:
    """Port of TSShape::rearrangeStates (tsShapeOldRead.cc:766).

    Pre-v17 files store animation state keyframe-major -- all of keyframe 0's
    channels, then all of keyframe 1's.  Every later version stores it
    channel-major, one channel's whole track at a time, which is what the
    assembler and the base indices expect.  This is that transpose, in place.
    """
    if a * b == 0:
        return
    base = region + start * size
    end = base + a * b * size
    if end > len(buf):
        raise DtsError(f"keyframe state block runs past the buffer ({end} > {len(buf)})")
    copy = bytes(buf[base:end])
    for i in range(a):
        for j in range(b):
            dst = region + size * (start + j * a + i)
            src = size * (i * b + j)
            buf[dst : dst + size] = copy[src : src + size]


def _rearrange_keyframe_data(
    seq,
    keyframes: list[tuple[int, int, int]],
    kf_start: int,
    w: WriteAlloc,
    node_state_start32: int,
    node_state_start16: int,
    object_state_start: int,
    decal_state_start: int,
) -> None:
    """Port of TSShape::rearrangeKeyframeData (tsShapeOldRead.cc:697).

    Recovers the sequence's base state indices from the keyframe table -- pre-17
    sequence records have no base fields -- and transposes the three state
    arrays out of keyframe-major order.  The indices are still in file space
    (they count the default node states); the assembler rebases them, the same
    way it does for every other v<22 shape.
    """
    if not seq.num_keyframes:
        return
    if not 0 <= kf_start < len(keyframes):
        raise DtsError(f"sequence names keyframe {kf_start}, table has {len(keyframes)}")
    num_nodes = seq.rotation_matters.count()
    num_objects = object_membership(seq).count()
    num_decals = seq.decal_matters.count()
    first_node_state, first_object_state, first_decal_state = keyframes[kf_start]

    seq.base_rotation = seq.base_translation = first_node_state if num_nodes else 0
    seq.base_object_state = first_object_state if num_objects else 0
    seq.base_decal_state = first_decal_state if num_decals else 0

    a = seq.num_keyframes
    # translations (3 x F32) and rotations (4 x S16) live in different buffers,
    # so each is a contiguous array of its own
    _rearrange_states(w.b32, node_state_start32, seq.base_translation, a, num_nodes, 12)
    _rearrange_states(w.b16, node_state_start16, seq.base_rotation, a, num_nodes, 8)
    _rearrange_states(w.b32, object_state_start, seq.base_object_state, a, num_objects, 12)
    _rearrange_states(w.b32, decal_state_start, seq.base_decal_state, a, num_decals, 4)


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

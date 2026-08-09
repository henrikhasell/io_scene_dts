"""Writer for pre-v19 DTS shapes (versions 15-18) — the flat-stream format.

The engine has no counterpart to this: TSShape::write only ever writes the
current version, and the old format exists in the engine solely as
TSShape::readOldShape (tsShapeOldRead.cc:390).  So this module is that reader
run backwards, statement by statement, and the comments name the read each
write feeds.

The flat format has no memory block and no guard words: it is one little-endian
stream, and the loader reassembles the three buffers from it.  Three things it
cannot express, all of them recomputed by the loader rather than lost:

- **mesh bounds, centres and radii** — filler in the buffer, then
  TSMesh::computeBounds (tsMesh.cc:3135).
- **parentMesh** — no field, so each detail level carries its own vertices.
- **sub-shape counts, smallest visible detail, and the runtime sibling links**
  — all derived from what is stored.

Two things it does lose: merge indices (the loader writes a zero count) and the
LOD error metrics (it writes -1).  ``fit_to_version`` drops both with a warning
before it gets here.

Versions 15 and 16 add one more inversion each.  v16 and older index meshes
through a separate list rather than a null type word; v15 and older store
animation keyframe-major, addressed through the obsolete keyframe table, so
every sequence's block of node, object and decal states is transposed on the
way out (the read's ``_rearrange_states``, backwards).
"""

from __future__ import annotations

from .errors import DtsWriteError
from .fit import sequences_for_version
from .matlist import write_material_list
from .sequence_io import object_membership, write_sequence
from .stream import StreamWriter
from .types import (
    DECAL_MESH,
    NULL_MESH,
    SKIN_MESH,
    SORTED_MESH,
    DecalMeshData,
    Mesh,
    Shape,
    SortedData,
)

OLD_MAX_VERSION = 18


def write_old_shape(shape: Shape, version: int, exporter_version: int) -> bytes:
    if version > OLD_MAX_VERSION:
        raise DtsWriteError(f"version {version} is not the flat-stream format")

    w = StreamWriter()
    w.u32((version | (exporter_version << 16)) & 0xFFFFFFFF)

    # bounds block, raw F32s
    w.f32(shape.radius)
    w.f32(shape.tube_radius)
    for c in shape.center:
        w.f32(c)
    for c in shape.bounds:
        w.f32(c)

    w.s32(len(shape.nodes))
    for node in shape.nodes:
        w.s32(node.name_index)
        w.s32(node.parent_index)
        if version < 17:
            w.u8(0)  # obsolete per-node bool, read and thrown away (:437)

    w.s32(len(shape.objects))
    for obj in shape.objects:
        w.s32(obj.name_index)
        w.s32(obj.num_meshes)
        w.s32(obj.start_mesh_index)
        w.s32(obj.node_index)

    w.s32(len(shape.decals))
    for decal in shape.decals:
        for value in decal.raw[:4]:
            w.s32(value)

    w.s32(len(shape.ifl_materials))
    for ifl in shape.ifl_materials:
        for value in ifl.raw[:2]:
            w.s32(value)

    # the three sub-shape "first" arrays, each behind its own count (:494)
    for firsts in (
        shape.sub_shape_first_node,
        shape.sub_shape_first_object,
        shape.sub_shape_first_decal,
    ):
        w.s32(len(shape.sub_shape_first_node))
        for value in firsts:
            w.s32(value)

    meshes = shape.meshes
    if version < 16:
        # one entry per mesh slot: the running mesh number, or -1 for a slot
        # that draws nothing (:528).  Null meshes are not in the stream at all
        w.s32(len(meshes))
        next_mesh = 0
        for mesh in meshes:
            if mesh is None:
                w.s32(-1)
            else:
                w.s32(next_mesh)
                next_mesh += 1
        meshes = [m for m in meshes if m is not None]

    node_rots, node_trans, object_states, decal_states, keyframes = _state_arrays(shape, version)

    if version < 17:
        w.s32(len(keyframes))
        for first_node, first_object, first_decal in keyframes:
            w.s32(first_node)
            w.s32(first_object)
            w.s32(first_decal)

    # node states: one interleaved rotation + translation per entry, the shape's
    # defaults first, then the animated tracks, then the ground frames (:546)
    if len(node_rots) != len(node_trans):
        raise DtsWriteError(
            f"{len(node_rots)} node rotation(s) but {len(node_trans)} translation(s): "
            f"version {version} stores them as one array (use fit_to_version)"
        )
    w.s32(len(node_rots))
    for quat, trans in zip(node_rots, node_trans):
        w.s16(quat.x)
        w.s16(quat.y)
        w.s16(quat.z)
        w.s16(quat.w)
        for c in trans:
            w.f32(c)

    w.s32(len(object_states))
    for state in object_states:
        w.f32(state.vis)
        w.s32(state.frame_index)
        w.s32(state.mat_frame_index)

    w.s32(len(decal_states))
    for state in decal_states:
        w.s32(state)

    w.s32(len(shape.triggers))
    for trigger in shape.triggers:
        w.u32(trigger.state)
        w.f32(trigger.pos)

    # details: the error metrics and poly count are not in the file (:581)
    w.s32(len(shape.details))
    for detail in shape.details:
        w.s32(detail.name_index)
        w.s32(detail.sub_shape_num)
        w.s32(detail.object_detail_num)
        w.f32(detail.size)

    w.s32(len(shape.sequences))
    for seq, start_keyframe in sequences_for_version(shape, version):
        write_sequence(w, seq, True, version, start_keyframe)

    w.s32(len(meshes))
    for mesh in meshes:
        w.s32(mesh.mesh_type & 0x7 if mesh is not None else NULL_MESH)
        _write_old_mesh(w, mesh, version)

    # names: an S32 length and the characters, no terminator (:625)
    w.s32(len(shape.names))
    for name in shape.names:
        w.s32_string(name)

    w.s32(1)  # gotList
    write_material_list(w, shape.materials, version)

    # skins ride in the mesh list, so the separate section is empty and the
    # detailFirstSkin table that would follow it is not written at all (:679)
    w.s32(0)
    return w.getvalue()


# ----------------------------------------------------------------------
# animation state, in the layout `version` reads
# ----------------------------------------------------------------------


def _state_arrays(shape: Shape, version: int):
    """The four state arrays as the file stores them, plus the keyframe table.

    For v17-18 the arrays go out as they are (with the shape's default node
    transforms in front).  For v15-16 each sequence's block is transposed into
    keyframe-major order and its base indices move into the keyframe table --
    the inverse of ``old_reader._rearrange_keyframe_data``.
    """
    node_rots = list(shape.default_rotations) + list(shape.node_rotations)
    node_trans = list(shape.default_translations) + list(shape.node_translations)
    object_states = list(shape.object_states)
    decal_states = list(shape.decal_states)
    keyframes: list[tuple[int, int, int]] = []
    if version >= 17:
        return node_rots, node_trans, object_states, decal_states, keyframes

    num_nodes = len(shape.nodes)
    for seq in shape.sequences:
        num_channels = seq.rotation_matters.count()
        num_objects = object_membership(seq).count()
        num_decals = seq.decal_matters.count()
        base_node = seq.base_rotation + num_nodes
        for k in range(seq.num_keyframes):
            keyframes.append(
                (
                    base_node + k * num_channels,
                    seq.base_object_state + k * num_objects,
                    seq.base_decal_state + k * num_decals,
                )
            )
        _to_keyframe_major(node_rots, base_node, seq.num_keyframes, num_channels)
        _to_keyframe_major(node_trans, base_node, seq.num_keyframes, num_channels)
        _to_keyframe_major(object_states, seq.base_object_state, seq.num_keyframes, num_objects)
        _to_keyframe_major(decal_states, seq.base_decal_state, seq.num_keyframes, num_decals)
    return node_rots, node_trans, object_states, decal_states, keyframes


def _to_keyframe_major(arr: list, start: int, num_keyframes: int, num_channels: int) -> None:
    """Transpose one sequence's block: channel-major in, keyframe-major out."""
    total = num_keyframes * num_channels
    if total == 0:
        return
    if start < 0 or start + total > len(arr):
        raise DtsWriteError(
            f"a sequence's state block ({start}..{start + total}) runs past the "
            f"{len(arr)} state(s) the shape has"
        )
    block = arr[start : start + total]
    for i in range(num_keyframes):
        for j in range(num_channels):
            arr[start + i * num_channels + j] = block[j * num_keyframes + i]


# ----------------------------------------------------------------------
# meshes
# ----------------------------------------------------------------------


def _write_old_mesh(w: StreamWriter, mesh: Mesh | None, version: int) -> None:
    """Inverse of readAllocMesh (tsShapeOldRead.cc:192)."""
    if mesh is None:
        return

    decal = mesh.decal_data if mesh.mesh_type == DECAL_MESH else None
    # a decal is an empty mesh whose primitives and indices are the decal's:
    # that is where the reader picks them up from (tsDecal.cc:169)
    primitives = decal.primitives if decal else mesh.primitives
    indices = decal.indices if decal else mesh.indices
    verts = [] if decal else mesh.verts
    tverts = [] if decal else mesh.tverts
    norms = [] if decal else mesh.norms

    w.s32(mesh.num_frames)
    w.s32(mesh.num_mat_frames)

    w.s32(len(verts))
    for v in verts:
        for c in v:
            w.f32(c)

    w.s32(len(tverts))
    for v in tverts:
        for c in v:
            w.f32(c)

    # the normal count is in the stream even though it always equals the vert
    # count -- "we could assume same as verts, but apparently in file" (:212)
    w.s32(len(norms))
    for v in norms:
        for c in v:
            w.f32(c)

    w.s32(len(primitives))
    for p in primitives:
        if version < 18:
            w.s32(p.start)
            w.s32(p.num_elements)
        else:
            w.s16(p.start)
            w.s16(p.num_elements)
        w.u32(p.mat_index)

    w.s32(len(indices))
    for index in indices:
        if version < 18:
            w.u32(index)
        else:
            w.s16(index if index < 0x8000 else index - 0x10000)

    # no mergeIndices field: the reader writes a zero count into the buffer
    w.s32(0 if decal else mesh.verts_per_frame)
    w.u32(0 if decal else mesh.flags)

    if mesh.mesh_type == SKIN_MESH:
        _write_old_skin(w, mesh)
    elif mesh.mesh_type == SORTED_MESH:
        _write_old_sorted(w, mesh)
    elif decal is not None:
        _write_old_decal(w, decal, version)


def _write_old_skin(w: StreamWriter, mesh: Mesh) -> None:
    w.s32(len(mesh.initial_verts))
    for v in mesh.initial_verts:
        for c in v:
            w.f32(c)

    w.s32(len(mesh.initial_norms))
    for v in mesh.initial_norms:
        for c in v:
            w.f32(c)

    w.s32(len(mesh.initial_transforms))
    for t in mesh.initial_transforms:
        for c in t:
            w.f32(c)

    w.s32(len(mesh.vertex_index))
    for i in mesh.vertex_index:
        w.s32(i)

    w.s32(len(mesh.bone_index))
    for i in mesh.bone_index:
        w.s32(i)

    # nodeIndex comes before the weights in the stream, and the reader shuffles
    # them back into the buffer's order (:295)
    w.s32(len(mesh.node_index))
    for i in mesh.node_index:
        w.s32(i)

    w.s32(len(mesh.weight))
    for f in mesh.weight:
        w.f32(f)


def _write_old_sorted(w: StreamWriter, mesh: Mesh) -> None:
    sd = mesh.sorted_data or SortedData()
    w.s32(len(sd.clusters))
    for cluster in sd.clusters:
        for word in cluster:
            w.s32(word)
    for array in (sd.start_cluster, sd.first_verts, sd.num_verts, sd.first_tverts):
        w.s32(len(array))
        for value in array:
            w.s32(value)
    w.u8(1 if sd.always_write_depth else 0)


def _write_old_decal(w: StreamWriter, dd: DecalMeshData, version: int) -> None:
    w.s32(len(dd.start_primitive))
    for value in dd.start_primitive:
        w.s32(value)
    if version >= 17:
        # obsolete startTVerts and tvertIndex, read and thrown away (:350)
        w.s32(0)
        w.s32(0)
    w.s32(dd.material_index)

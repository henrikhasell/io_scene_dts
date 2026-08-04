"""DTS shape writer, versions 24 and 23.

Port of TSShape::write + disassembleShape (tsShape.cc:1203/:1063).

The single difference between the two versions: v23 omits the numGroundFrames
count word, the ground translation/rotation arrays, AND their guard word.
Writing v23 with ground frames present is refused — dropping them silently
would strip the speed off every movement animation (PlayerData::getGroundInfo).
"""

from __future__ import annotations

from .errors import DtsWriteError
from .matlist import write_material_list
from .mesh_io import write_mesh
from .sequence_io import write_sequence
from .stream import StreamWriter, WriteAlloc
from .types import Shape


def write_shape(shape: Shape, version: int = 24, exporter_version: int | None = None) -> bytes:
    if version not in (23, 24):
        raise DtsWriteError(
            f"cannot write DTS version {version}: only 24 (Torque) and 23 (Tribes 2) "
            f"are supported — older versions keep skins in a separate section"
        )
    if version <= 23 and shape.ground_translations:
        raise DtsWriteError(
            f"this shape has {len(shape.ground_translations)} ground frame(s) and "
            f"version {version} has nowhere to keep them; writing it would drop the "
            f"speed off every movement animation (use strip_ground_frames first)"
        )

    if exporter_version is None:
        exporter_version = shape.exporter_version

    alloc = WriteAlloc()
    _disassemble_shape(shape, alloc, version)
    block, size_mem_buffer, start16, start8 = alloc.to_memory_block(shape.pad16, shape.pad8)

    w = StreamWriter()
    w.u32((version | (exporter_version << 16)) & 0xFFFFFFFF)
    w.u32(size_mem_buffer)
    w.u32(start16)
    w.u32(start8)
    w.raw(block)

    w.s32(len(shape.sequences))
    for seq in shape.sequences:
        write_sequence(w, seq, write_name_index=True)

    write_material_list(w, shape.materials)
    return w.getvalue()


def write_shape_file(shape: Shape, path, version: int = 24, exporter_version: int | None = None) -> None:
    data = write_shape(shape, version, exporter_version)
    with open(path, "wb") as f:
        f.write(data)


def strip_ground_frames(shape: Shape) -> None:
    """Explicitly remove ground-frame data so the shape can be written as v23.

    Clears the ground arrays and zeroes every sequence's ground fields.
    """
    shape.ground_translations = []
    shape.ground_rotations = []
    for seq in shape.sequences:
        seq.first_ground_frame = 0
        seq.num_ground_frames = 0


def _disassemble_shape(shape: Shape, alloc: WriteAlloc, version: int) -> None:
    num_nodes = len(shape.nodes)
    num_sub_shapes = len(shape.sub_shape_first_node)

    # counts
    alloc.set32(num_nodes)
    alloc.set32(len(shape.objects))
    alloc.set32(len(shape.decals))
    alloc.set32(num_sub_shapes)
    alloc.set32(len(shape.ifl_materials))
    alloc.set32(len(shape.node_rotations))
    alloc.set32(len(shape.node_translations))
    alloc.set32(len(shape.node_uniform_scales))
    alloc.set32(len(shape.node_aligned_scales))
    alloc.set32(len(shape.node_arbitrary_scale_factors))
    if version > 23:
        alloc.set32(len(shape.ground_translations))
    alloc.set32(len(shape.object_states))
    alloc.set32(len(shape.decal_states))
    alloc.set32(len(shape.triggers))
    alloc.set32(len(shape.details))
    alloc.set32(len(shape.meshes))
    alloc.set32(len(shape.names))
    alloc.set32(int(shape.smallest_visible_size))  # engine writes (S32) cast
    alloc.set32(shape.smallest_visible_dl)
    alloc.guard()

    # bounds
    alloc.set32f(shape.radius)
    alloc.set32f(shape.tube_radius)
    alloc.set32fn(shape.center)
    alloc.set32fn(shape.bounds)
    alloc.guard()

    # tables
    for n in shape.nodes:
        alloc.set32n((n.name_index, n.parent_index) + tuple(n.runtime))
    alloc.guard()
    for o in shape.objects:
        alloc.set32n((o.name_index, o.num_meshes, o.start_mesh_index, o.node_index) + tuple(o.runtime))
    alloc.guard()
    for d in shape.decals:
        alloc.set32n(d.raw)
    alloc.guard()
    for m in shape.ifl_materials:
        alloc.set32n(m.raw)
    alloc.guard()
    alloc.set32n(shape.sub_shape_first_node)
    alloc.set32n(shape.sub_shape_first_object)
    alloc.set32n(shape.sub_shape_first_decal)
    alloc.guard()
    alloc.set32n(shape.sub_shape_num_nodes)
    alloc.set32n(shape.sub_shape_num_objects)
    alloc.set32n(shape.sub_shape_num_decals)
    alloc.guard()

    # default + animated transforms
    alloc.set16n([c for q in shape.default_rotations for c in (q.x, q.y, q.z, q.w)])
    alloc.set32fn([c for t in shape.default_translations for c in t])
    alloc.set16n([c for q in shape.node_rotations for c in (q.x, q.y, q.z, q.w)])
    alloc.set32fn([c for t in shape.node_translations for c in t])
    alloc.guard()

    # scales
    alloc.set32fn(shape.node_uniform_scales)
    alloc.set32fn([c for t in shape.node_aligned_scales for c in t])
    alloc.set32fn([c for t in shape.node_arbitrary_scale_factors for c in t])
    alloc.set16n([c for q in shape.node_arbitrary_scale_rots for c in (q.x, q.y, q.z, q.w)])
    alloc.guard()

    # ground frames: v24 only — the guard too (tsShape.cc:1139)
    if version > 23:
        alloc.set32fn([c for t in shape.ground_translations for c in t])
        alloc.set16n([c for q in shape.ground_rotations for c in (q.x, q.y, q.z, q.w)])
        alloc.guard()

    # object states
    for s in shape.object_states:
        alloc.set32f(s.vis)
        alloc.set32(s.frame_index)
        alloc.set32(s.mat_frame_index)
    alloc.guard()

    alloc.set32n(shape.decal_states)
    alloc.guard()

    for t in shape.triggers:
        alloc.setu32(t.state)
        alloc.set32f(t.pos)
    alloc.guard()

    for d in shape.details:
        alloc.set32(d.name_index)
        alloc.set32(d.sub_shape_num)
        alloc.set32(d.object_detail_num)
        alloc.set32f(d.size)
        alloc.set32f(d.average_error)
        alloc.set32f(d.max_error)
        alloc.set32(d.poly_count)
    alloc.guard()

    # meshes
    for mesh in shape.meshes:
        write_mesh(alloc, mesh)
    alloc.guard()

    # names
    for name in shape.names:
        alloc.set_cstring8(name)
    alloc.guard()

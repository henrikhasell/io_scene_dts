"""DTS shape writer, versions 15-24.

Port of TSShape::write + disassembleShape (tsShape.cc:1203/:1063), which the
engine only ever runs at the current version -- so for anything older the
reference is TSShape::read run backwards, and every branch here cites the read
it inverts.

This module writes the three-buffer memory-block format, versions 19-24.
Versions 15-18 are the flat-stream format and live in ``old_writer``.  What the
requested version has no room for is the caller's problem before it gets here:
``fit.check_representable`` refuses rather than lose it quietly, and
``fit_to_version`` is how a caller says "lose it, but tell me".

Where the versions differ, going down from 24:

- **24 -> 23**: the numGroundFrames count, the ground arrays and their guard
  word disappear (:1139).
- **23 -> 22**: a numSkins count word joins the header, and the detailFirstSkin
  and detailNumSkins tables join the tail, followed by the skins themselves.
  Nothing is written into that section: a skin goes in the ordinary mesh list,
  which every version's reader accepts (tsShape.cc:875) and which keeps the
  object's name and node index -- the old section has nowhere for either, and
  the loader invents an object per skin with neither (fixupOldSkins,
  tsShapeOldRead.cc:783).
- **22 -> 21**: the five node-state counts collapse to one, defaults included;
  scale animation vanishes; ground frames move to the end of the node arrays;
  encoded normals leave the meshes; sequences lose the flags word and gain
  three bools.
- **21 -> 20**: material reflection amounts disappear.
- **20 -> 19**: decal meshes regain the empty mesh header they had when decals
  were derived from meshes (tsDecal.cc:169).
"""

from __future__ import annotations

from .fit import (
    MAX_VERSION,
    MIN_VERSION,
    check_representable,
    check_version,
    fit_to_version,
    sequences_for_version,
    strip_ground_frames,
)
from .matlist import write_material_list
from .mesh_io import write_mesh
from .sequence_io import write_sequence
from .stream import StreamWriter, WriteAlloc
from .types import Shape

# the oldest version this module handles; below it the file is a flat stream
MIN_BLOCK_VERSION = 19

__all__ = [
    "write_shape",
    "write_shape_file",
    "fit_to_version",
    "strip_ground_frames",
    "MIN_VERSION",
    "MAX_VERSION",
]


def write_shape(shape: Shape, version: int = 24, exporter_version: int | None = None) -> bytes:
    check_version(version)
    check_representable(shape, version)

    if exporter_version is None:
        exporter_version = shape.exporter_version

    if version < MIN_BLOCK_VERSION:
        from .old_writer import write_old_shape

        return write_old_shape(shape, version, exporter_version)

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
    for seq, start_keyframe in sequences_for_version(shape, version):
        write_sequence(w, seq, True, version, start_keyframe)

    write_material_list(w, shape.materials, version)
    return w.getvalue()


def write_shape_file(shape: Shape, path, version: int = 24, exporter_version: int | None = None) -> None:
    data = write_shape(shape, version, exporter_version)
    with open(path, "wb") as f:
        f.write(data)


def _disassemble_shape(shape: Shape, alloc: WriteAlloc, version: int) -> None:
    num_nodes = len(shape.nodes)
    num_sub_shapes = len(shape.sub_shape_first_node)

    # counts
    alloc.set32(num_nodes)
    alloc.set32(len(shape.objects))
    alloc.set32(len(shape.decals))
    alloc.set32(num_sub_shapes)
    alloc.set32(len(shape.ifl_materials))
    if version < 22:
        # one count for the whole node-state array, defaults included
        alloc.set32(num_nodes + len(shape.node_rotations))
    else:
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
    if version < 23:
        alloc.set32(0)  # numSkins: skins ride in the mesh list, see the docstring
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

    # default + animated transforms.  Pre-v22 this is one array of node states
    # with the defaults in front -- which is what these four writes already are,
    # because the 16- and 32-bit halves are separate buffers
    alloc.set16n([c for q in shape.default_rotations for c in (q.x, q.y, q.z, q.w)])
    alloc.set32fn([c for t in shape.default_translations for c in t])
    alloc.set16n([c for q in shape.node_rotations for c in (q.x, q.y, q.z, q.w)])
    alloc.set32fn([c for t in shape.node_translations for c in t])
    alloc.guard()

    # scales: v22 and up only, the guard too (tsShape.cc:744)
    if version > 21:
        alloc.set32fn(shape.node_uniform_scales)
        alloc.set32fn([c for t in shape.node_aligned_scales for c in t])
        alloc.set32fn([c for t in shape.node_arbitrary_scale_factors for c in t])
        alloc.set16n([c for q in shape.node_arbitrary_scale_rots for c in (q.x, q.y, q.z, q.w)])
        alloc.guard()

    # ground frames: v24 only — the guard too (tsShape.cc:1139).  Below v22 they
    # are already at the tail of the node arrays written above
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
        write_mesh(alloc, mesh, version)
    alloc.guard()

    # names
    for name in shape.names:
        alloc.set_cstring8(name)
    alloc.guard()

    if version < 23:
        # the skin section: empty, but its tables and both guards are not
        # optional (tsShape.cc:970)
        alloc.set32n([0] * len(shape.details))  # detailFirstSkin
        alloc.set32n([0] * len(shape.details))  # detailNumSkins
        alloc.guard()
        alloc.guard()

"""DTS shape reader.

Versions 19-24 use the three-buffer memory-block format handled here;
versions 17-18 are delegated to old_reader (the flat-stream format).

Port of TSShape::read + assembleShape (tsShape.cc:1295/:616).  Old files are
normalized to the modern in-memory layout on load:

- v<22: ground frames are migrated out of the node-state arrays and the
  sequences' base indices rebased (tsShape.cc:772).
- v<23: the separate skin section is merged into the mesh/object lists via a
  port of fixupOldSkins (tsShapeOldRead.cc:783).
"""

from __future__ import annotations

import struct

from .errors import DtsError, DtsUnsupportedVersion
from .matlist import read_material_list
from .mesh_io import read_mesh, _read_base_mesh, _read_skin_extension
from .primitives import Quat16
from .sequence_io import read_sequence
from .stream import ReadAlloc, StreamReader
from .types import (
    SKIN_MESH,
    Decal,
    Detail,
    IflMaterial,
    Mesh,
    Node,
    Object,
    ObjectState,
    Shape,
    Trigger,
)

MIN_VERSION = 19
MAX_VERSION = 24


def read_header(data: bytes) -> tuple[int, int]:
    """Return (version, exporter_version) without parsing the shape."""
    if len(data) < 4:
        raise DtsError(f"file too short ({len(data)} bytes)")
    r = StreamReader(data)
    word = r.u32()
    return word & 0xFF, word >> 16


def read_shape(data: bytes) -> Shape:
    try:
        return _read_shape(data)
    except struct.error as e:
        raise DtsError(f"truncated or corrupt DTS file: {e}") from e


def _read_shape(data: bytes) -> Shape:
    if len(data) < 16:
        raise DtsError(f"file too short ({len(data)} bytes)")
    r = StreamReader(data)
    word = r.u32()
    version = word & 0xFF
    exporter_version = word >> 16
    if version < MIN_VERSION:
        # 17-18 use the old flat-stream format; below that is refused
        from .old_reader import read_old_shape

        return read_old_shape(data, version, exporter_version)
    if version > MAX_VERSION:
        raise DtsUnsupportedVersion(version)

    size_mem_buffer = r.u32()
    start16 = r.u32()
    start8 = r.u32()
    if not (start16 <= start8 <= size_mem_buffer):
        raise DtsError(f"bad buffer offsets: start16={start16} start8={start8} size={size_mem_buffer}")
    block = r.raw(size_mem_buffer * 4)
    buf32 = block[: start16 * 4]
    buf16 = block[start16 * 4 : start8 * 4]
    buf8 = block[start8 * 4 :]

    shape = Shape(source_version=version, exporter_version=exporter_version)

    num_sequences = r.s32()
    shape.sequences = [read_sequence(r, version, read_name_index=True) for _ in range(num_sequences)]
    shape.materials = read_material_list(r, version)

    alloc = ReadAlloc(buf32, buf16, buf8)
    _assemble_shape(shape, alloc, version)
    # remaining bytes are the writer's dword padding (uninitialized memory in
    # engine-written files) — kept so a write can reuse them rather than
    # inventing padding of its own
    shape.pad16 = bytes(buf16[alloc.p16 :])
    shape.pad8 = bytes(buf8[alloc.p8 :])
    return shape


def read_shape_file(path) -> Shape:
    with open(path, "rb") as f:
        return read_shape(f.read())


def _assemble_shape(shape: Shape, alloc: ReadAlloc, version: int) -> None:
    # counts
    num_nodes = alloc.get32()
    num_objects = alloc.get32()
    num_decals = alloc.get32()
    num_sub_shapes = alloc.get32()
    num_ifl_materials = alloc.get32()
    if version < 22:
        # one combined count: defaults + animated states share the arrays
        num_node_rots = num_node_trans = alloc.get32() - num_nodes
        num_uniform = num_aligned = num_arbitrary = 0
    else:
        num_node_rots = alloc.get32()
        num_node_trans = alloc.get32()
        num_uniform = alloc.get32()
        num_aligned = alloc.get32()
        num_arbitrary = alloc.get32()
    num_ground_frames = alloc.get32() if version > 23 else 0
    num_object_states = alloc.get32()
    num_decal_states = alloc.get32()
    num_triggers = alloc.get32()
    num_details = alloc.get32()
    num_meshes = alloc.get32()
    num_skins = alloc.get32() if version < 23 else 0
    num_names = alloc.get32()
    shape.smallest_visible_size = float(alloc.get32())
    shape.smallest_visible_dl = alloc.get32()

    alloc.guard()

    # bounds
    shape.radius = alloc.get32f()
    shape.tube_radius = alloc.get32f()
    shape.center = alloc.get32fn(3)
    shape.bounds = alloc.get32fn(6)

    alloc.guard()

    # nodes / objects / decals / ifl materials
    flat = alloc.get32n(num_nodes * 5)
    shape.nodes = [
        Node(flat[i], flat[i + 1], (flat[i + 2], flat[i + 3], flat[i + 4]))
        for i in range(0, len(flat), 5)
    ]
    alloc.guard()

    flat = alloc.get32n(num_objects * 6)
    shape.objects = [
        Object(flat[i], flat[i + 1], flat[i + 2], flat[i + 3], (flat[i + 4], flat[i + 5]))
        for i in range(0, len(flat), 6)
    ]
    alloc.guard()

    flat = alloc.get32n(num_decals * 5)
    shape.decals = [Decal(tuple(flat[i : i + 5])) for i in range(0, len(flat), 5)]
    alloc.guard()

    flat = alloc.get32n(num_ifl_materials * 5)
    shape.ifl_materials = [IflMaterial(tuple(flat[i : i + 5])) for i in range(0, len(flat), 5)]
    alloc.guard()

    shape.sub_shape_first_node = list(alloc.get32n(num_sub_shapes))
    shape.sub_shape_first_object = list(alloc.get32n(num_sub_shapes))
    shape.sub_shape_first_decal = list(alloc.get32n(num_sub_shapes))
    alloc.guard()

    shape.sub_shape_num_nodes = list(alloc.get32n(num_sub_shapes))
    shape.sub_shape_num_objects = list(alloc.get32n(num_sub_shapes))
    shape.sub_shape_num_decals = list(alloc.get32n(num_sub_shapes))
    alloc.guard()

    # default + animated node transforms
    flat16 = alloc.get16n(4 * num_nodes)
    shape.default_rotations = [Quat16(*flat16[i : i + 4]) for i in range(0, len(flat16), 4)]
    flat = alloc.get32fn(3 * num_nodes)
    shape.default_translations = [tuple(flat[i : i + 3]) for i in range(0, len(flat), 3)]
    flat = alloc.get32fn(3 * num_node_trans)
    shape.node_translations = [tuple(flat[i : i + 3]) for i in range(0, len(flat), 3)]
    flat16 = alloc.get16n(4 * num_node_rots)
    shape.node_rotations = [Quat16(*flat16[i : i + 4]) for i in range(0, len(flat16), 4)]
    alloc.guard()

    if version > 21:
        shape.node_uniform_scales = list(alloc.get32fn(num_uniform))
        flat = alloc.get32fn(3 * num_aligned)
        shape.node_aligned_scales = [tuple(flat[i : i + 3]) for i in range(0, len(flat), 3)]
        flat = alloc.get32fn(3 * num_arbitrary)
        shape.node_arbitrary_scale_factors = [tuple(flat[i : i + 3]) for i in range(0, len(flat), 3)]
        flat16 = alloc.get16n(4 * num_arbitrary)
        shape.node_arbitrary_scale_rots = [Quat16(*flat16[i : i + 4]) for i in range(0, len(flat16), 4)]
        alloc.guard()

    if version < 22:
        # migrate ground frames out of the node-state arrays (tsShape.cc:772)
        for seq in shape.sequences:
            old = len(shape.ground_translations)
            first = seq.first_ground_frame - num_nodes
            n = seq.num_ground_frames
            shape.ground_translations.extend(shape.node_translations[first : first + n])
            shape.ground_rotations.extend(shape.node_rotations[first : first + n])
            seq.first_ground_frame = old
            seq.base_translation -= num_nodes
            seq.base_rotation -= num_nodes
            seq.base_scale = 0

    if version > 23:
        flat = alloc.get32fn(3 * num_ground_frames)
        shape.ground_translations = [tuple(flat[i : i + 3]) for i in range(0, len(flat), 3)]
        flat16 = alloc.get16n(4 * num_ground_frames)
        shape.ground_rotations = [Quat16(*flat16[i : i + 4]) for i in range(0, len(flat16), 4)]
        alloc.guard()

    # object states (vis is a bit-cast F32 in the first slot)
    states = []
    for _ in range(num_object_states):
        vis = alloc.get32f()
        frame = alloc.get32()
        mat_frame = alloc.get32()
        states.append(ObjectState(vis, frame, mat_frame))
    shape.object_states = states
    alloc.guard()

    shape.decal_states = list(alloc.get32n(num_decal_states))
    alloc.guard()

    triggers = []
    for _ in range(num_triggers):
        state = alloc.getu32()
        pos = alloc.get32f()
        triggers.append(Trigger(state, pos))
    shape.triggers = triggers
    alloc.guard()

    details = []
    for _ in range(num_details):
        name_index = alloc.get32()
        sub_shape = alloc.get32()
        obj_detail = alloc.get32()
        size = alloc.get32f()
        avg_err = alloc.get32f()
        max_err = alloc.get32f()
        poly_count = alloc.get32()
        details.append(Detail(name_index, sub_shape, obj_detail, size, avg_err, max_err, poly_count))
    shape.details = details
    alloc.guard()

    # meshes
    meshes: list[Mesh | None] = []
    for _ in range(num_meshes):
        meshes.append(read_mesh(alloc, version, meshes))
    shape.meshes = meshes
    alloc.guard()

    # names (NUL-terminated in the 8-bit buffer)
    shape.names = [alloc.get_cstring8() for _ in range(num_names)]
    alloc.guard()

    if version < 23:
        detail_first_skin = list(alloc.get32n(num_details))
        detail_num_skins = list(alloc.get32n(num_details))
        alloc.guard()

        # the skin section has no type words: each entry is a TSSkinMesh payload
        skins: list[Mesh] = []
        for _ in range(num_skins):
            skin = _read_base_mesh(alloc, version, skins, SKIN_MESH)
            _read_skin_extension(alloc, version, skin, skins)
            skins.append(skin)
        alloc.guard()

        _fixup_old_skins(shape, num_meshes, skins, detail_first_skin, detail_num_skins)


def _fixup_old_skins(shape: Shape, num_meshes: int, skins: list, detail_first_skin, detail_num_skins) -> None:
    """Port of TSShape::fixupOldSkins: fold the pre-v23 skin section into the
    object/mesh lists so the in-memory model is uniform."""
    if not skins:
        return

    num_details = len(shape.details)
    num_objects = len(shape.objects)
    remaining: list[Mesh | None] = list(skins)
    skins_copy: list[Mesh | None] = []
    num_skin_objects = 0
    skins_used = 0

    while skins_used < len(skins):
        obj = Object(
            name_index=0,
            num_meshes=0,
            start_mesh_index=num_meshes + len(skins_copy),
            node_index=-1,
            runtime=(-1, -1),
        )
        shape.objects.append(obj)
        num_skin_objects += 1
        for dl in range(num_details):
            first = detail_first_skin[dl]
            if first < 0:
                raise DtsError("negative detailFirstSkin")
            end = min(len(skins), first + detail_num_skins[dl])
            found = False
            for i in range(first, end):
                if remaining[i] is not None:
                    skins_copy.append(remaining[i])
                    remaining[i] = None
                    obj.num_meshes += 1
                    skins_used += 1
                    found = True
                    break
            if not found:
                skins_copy.append(None)
                obj.num_meshes += 1
        while skins_copy and skins_copy[-1] is None:
            skins_copy.pop()
            obj.num_meshes -= 1
        if obj.num_meshes == 0:
            shape.objects.pop()
            num_skin_objects -= 1

    shape.meshes.extend(skins_copy)

    if len(shape.sub_shape_first_object) == 1:
        shape.sub_shape_num_objects[0] += num_skin_objects

    # default states for the new objects go right after the first numObjects states
    inserted = [ObjectState(1.0, 0, 0) for _ in range(num_skin_objects)]
    shape.object_states[num_objects:num_objects] = inserted
    for seq in shape.sequences:
        seq.base_object_state += num_skin_objects

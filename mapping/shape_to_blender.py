"""Build a Blender scene from a dtslib.Shape.

Mapping conventions (mirrored by blender_to_shape):

- One armature per shape; every DTS node is a bone (rest pose from the
  quantized default transforms, quaternions conjugated at this boundary).
  Bone custom props: dts_node_index, dts_name.
- One mesh object per (DTS object, detail level), named "<object><size>"
  (Torque's detail-suffix convention).  Object custom props: dts_object_name,
  dts_subshape, dts_detail_size, dts_node_index.
- Rigid meshes are parented to their node's bone; skins get vertex groups +
  an armature modifier.
- Meshes Blender can't faithfully rebuild (sorted, multi-frame) carry a
  pickled dtslib.Mesh in dts_frozen_payload and are re-emitted verbatim.
- Decal meshes are dropped with a warning (dead legacy feature).
- Detail levels are organized into collections named after the detail.
"""

from __future__ import annotations

import base64
import json
import pickle
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

from ..dtslib import Shape
from ..dtslib.types import (
    DECAL_MESH,
    MESH_BILLBOARD,
    MESH_BILLBOARD_Z_AXIS,
    PRIM_FAN,
    PRIM_MATERIAL_MASK,
    PRIM_NO_MATERIAL,
    PRIM_STRIP,
    PRIM_TYPE_MASK,
    SKIN_MESH,
    SORTED_MESH,
    Mesh,
)

from .materials import material_to_blender
from .naming import object_display_name
from .sequences import dts_local_matrix, import_sequences

BONE_LENGTH = 0.25


def shape_to_blender(
    shape: Shape,
    name: str,
    context,
    filepath: str | None = None,
    do_import_sequences: bool = True,
    create_materials: bool = True,
) -> tuple[bpy.types.Object, list[str]]:
    """Build the scene; returns (armature object, warnings)."""
    warnings = []
    scene_coll = context.scene.collection

    arm_obj = _build_armature(shape, name, context)
    scene_coll.objects.link(arm_obj)
    context.view_layer.objects.active = arm_obj
    _fill_armature_bones(shape, arm_obj)

    bone_name_by_node = {
        int(b["dts_node_index"]): b.name for b in arm_obj.data.bones if "dts_node_index" in b
    }

    bmats = []
    if create_materials:
        search_dir = Path(filepath).parent if filepath else None
        bmats = [
            material_to_blender(m, i, shape.materials, search_dir)
            for i, m in enumerate(shape.materials)
        ]

    detail_collections = {}
    node_mats = _node_armature_matrices(shape)

    for obj_index, obj in enumerate(shape.objects):
        base_name = shape.name(obj.name_index)
        subshape = _subshape_of_object(shape, obj_index)
        for j in range(obj.num_meshes):
            mesh = shape.meshes[obj.start_mesh_index + j]
            if mesh is None:
                continue
            if mesh.mesh_type == DECAL_MESH:
                warnings.append(
                    f"object {base_name!r}: decal mesh dropped (not supported in Blender round-trip)"
                )
                continue
            detail = _detail_for(shape, subshape, j)
            size = int(detail.size) if detail else j
            display = object_display_name(base_name or "object", size)
            bobj = _build_mesh_object(shape, mesh, display, bmats, warnings)
            bobj["dts_object_name"] = base_name
            bobj["dts_subshape"] = subshape
            bobj["dts_detail_size"] = size
            if detail is not None:
                bobj["dts_detail_name"] = shape.name(detail.name_index)
                bobj["dts_detail_odn"] = detail.object_detail_num
            bobj["dts_node_index"] = obj.node_index
            if obj_index < len(shape.object_states):
                st = shape.object_states[obj_index]
                if st.vis != 1.0:
                    bobj["dts_default_vis"] = st.vis

            coll_name = f"{name}.{shape.name(detail.name_index) if detail else f'detail{j}'}"
            coll = detail_collections.get(coll_name)
            if coll is None:
                coll = bpy.data.collections.new(coll_name)
                scene_coll.children.link(coll)
                detail_collections[coll_name] = coll
            coll.objects.link(bobj)

            if mesh.mesh_type == SKIN_MESH:
                _bind_skin(shape, mesh, bobj, arm_obj, bone_name_by_node)
            else:
                _parent_rigid(bobj, arm_obj, obj.node_index, bone_name_by_node, node_mats)

    arm_obj["dts_smallest_visible_size"] = shape.smallest_visible_size
    arm_obj["dts_smallest_visible_dl"] = shape.smallest_visible_dl
    arm_obj["dts_exporter_version"] = shape.exporter_version
    # full detail table (details can exist with no geometry at all)
    arm_obj["dts_details"] = json.dumps(
        [
            [shape.name(d.name_index), d.sub_shape_num, d.object_detail_num, d.size]
            for d in shape.details
        ]
    )
    # material list order — map slots and IFL entries index into it
    arm_obj["dts_materials_order"] = json.dumps([m.name for m in shape.materials])
    if shape.ifl_materials:
        arm_obj["dts_ifl_materials"] = json.dumps(
            [{"name": shape.name(m.raw[0]), "raw": list(m.raw)} for m in shape.ifl_materials]
        )
    if shape.decals:
        _store_decals(shape, arm_obj)

    if do_import_sequences and shape.sequences:
        import_sequences(shape, arm_obj, bone_name_by_node)

    return arm_obj, warnings


def _store_decals(shape: Shape, arm_obj) -> None:
    """Decals (legacy damage-overlay meshes) can't be edited in Blender, but
    they round-trip: records + mesh payloads keyed by owner object name, plus
    the shape's default decal states (the first numDecals entries)."""
    entries = []
    for d in shape.decals:
        name_index, num_meshes, start, obj_index, _sibling = d.raw
        slots = []
        for j in range(num_meshes):
            mesh = shape.meshes[start + j] if start + j < len(shape.meshes) else None
            slots.append(
                base64.b64encode(pickle.dumps(mesh)).decode("ascii") if mesh is not None else None
            )
        owner = shape.name(shape.objects[obj_index].name_index) if 0 <= obj_index < len(shape.objects) else ""
        entries.append({"name": shape.name(name_index), "object": owner, "meshes": slots})
    arm_obj["dts_decals"] = json.dumps(entries)
    arm_obj["dts_decal_states"] = json.dumps(shape.decal_states[: len(shape.decals)])


def geometry_digest(me) -> str:
    """Stable digest of a Blender mesh's geometry, used to detect edits to
    meshes that only round-trip as frozen payloads (sorted/multi-matframe)."""
    import hashlib

    h = hashlib.sha1()
    h.update(len(me.vertices).to_bytes(4, "little"))
    for v in me.vertices:
        h.update(b"%.4f%.4f%.4f" % tuple(v.co))
    for p in me.polygons:
        for vi in p.vertices:
            h.update(vi.to_bytes(4, "little"))
    return h.hexdigest()


# ----------------------------------------------------------------------
# armature
# ----------------------------------------------------------------------


def _node_armature_matrices(shape: Shape) -> list[Matrix]:
    mats = []
    for i, node in enumerate(shape.nodes):
        local = dts_local_matrix(shape.default_rotations[i], shape.default_translations[i])
        if node.parent_index >= 0:
            mats.append(mats[node.parent_index] @ local)
        else:
            mats.append(local)
    return mats


def _build_armature(shape: Shape, name: str, context) -> bpy.types.Object:
    arm = bpy.data.armatures.new(name)
    arm_obj = bpy.data.objects.new(name, arm)
    return arm_obj


def _fill_armature_bones(shape: Shape, arm_obj: bpy.types.Object) -> None:
    node_mats = _node_armature_matrices(shape)
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        eb = arm_obj.data.edit_bones
        created = []
        for i, node in enumerate(shape.nodes):
            bone = eb.new(shape.name(node.name_index) or f"node{i}")
            bone.head = (0.0, 0.0, 0.0)
            bone.tail = (0.0, BONE_LENGTH, 0.0)
            bone.matrix = node_mats[i]
            if node.parent_index >= 0:
                bone.parent = created[node.parent_index]
                bone.use_connect = False
            created.append(bone)
        created_names = [b.name for b in created]
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
    for i, bname in enumerate(created_names):
        bone = arm_obj.data.bones[bname]
        bone["dts_node_index"] = i
        bone["dts_name"] = shape.name(shape.nodes[i].name_index)


# ----------------------------------------------------------------------
# meshes
# ----------------------------------------------------------------------


def decode_primitives(mesh: Mesh) -> list[tuple[int, int, int, int]]:
    """Decode strips/fans/triangles into (a, b, c, material_word) triangles.

    DTS winding is clockwise-front; Blender's is counter-clockwise, so every
    triangle is reversed here (and reversed back on export).
    """
    tris = []
    for prim in mesh.primitives:
        ptype = prim.mat_index & PRIM_TYPE_MASK
        idx = mesh.indices[prim.start : prim.start + prim.num_elements]
        if ptype == PRIM_STRIP:
            for k in range(len(idx) - 2):
                a, b, c = idx[k], idx[k + 1], idx[k + 2]
                if a == b or b == c or a == c:
                    continue  # degenerate strip stitch
                if k & 1:
                    a, b = b, a
                tris.append((c, b, a, prim.mat_index))
        elif ptype == PRIM_FAN:
            for k in range(1, len(idx) - 1):
                tris.append((idx[k + 1], idx[k], idx[0], prim.mat_index))
        else:  # triangles
            for k in range(0, len(idx) - 2, 3):
                tris.append((idx[k + 2], idx[k + 1], idx[k], prim.mat_index))
    return tris


def _needs_freezing(mesh: Mesh) -> bool:
    # plain multi-frame meshes are handled via shape keys; only sorted meshes
    # and multi-matframe meshes still round-trip as frozen payloads
    return mesh.mesh_type == SORTED_MESH or mesh.num_mat_frames > 1


def _build_mesh_object(shape: Shape, mesh: Mesh, name: str, bmats, warnings) -> bpy.types.Object:
    bm = bpy.data.meshes.new(name)

    verts = mesh.verts or mesh.initial_verts
    norms = mesh.norms or mesh.initial_norms
    # multi-frame meshes: show frame 0 only
    if mesh.num_frames > 1 and mesh.verts_per_frame > 0:
        verts = verts[: mesh.verts_per_frame]

    tris = decode_primitives(mesh)
    nv = len(verts)
    tris = [t for t in tris if t[0] < nv and t[1] < nv and t[2] < nv]

    bm.from_pydata([Vector(v) for v in verts], [], [t[:3] for t in tris])

    # materials
    mat_slot = {}
    for a, b, c, mword in tris:
        key = -1 if mword & PRIM_NO_MATERIAL else mword & PRIM_MATERIAL_MASK
        if key not in mat_slot:
            mat_slot[key] = len(mat_slot)
            if key >= 0 and key < len(bmats):
                bm.materials.append(bmats[key])
            else:
                bm.materials.append(None)
    for poly, tri in zip(bm.polygons, tris):
        key = -1 if tri[3] & PRIM_NO_MATERIAL else tri[3] & PRIM_MATERIAL_MASK
        poly.material_index = mat_slot[key]

    # uvs (per-corner from per-vertex tverts)
    if mesh.tverts:
        uv = bm.uv_layers.new(name="UVMap")
        for loop in bm.loops:
            u, v = mesh.tverts[loop.vertex_index] if loop.vertex_index < len(mesh.tverts) else (0.0, 0.0)
            uv.data[loop.index].uv = (u, 1.0 - v)

    bm.validate()
    if norms and len(norms) >= len(bm.vertices):
        try:
            bm.normals_split_custom_set_from_vertices([Vector(n) for n in norms[: len(bm.vertices)]])
        except Exception:
            pass
    bm.update()

    bobj = bpy.data.objects.new(name, bm)
    bobj["dts_mesh_type"] = mesh.mesh_type
    if mesh.flags & MESH_BILLBOARD:
        bobj["dts_billboard"] = True
    if mesh.flags & MESH_BILLBOARD_Z_AXIS:
        bobj["dts_billboard_z"] = True
    if _needs_freezing(mesh):
        bobj["dts_frozen_payload"] = base64.b64encode(pickle.dumps(mesh)).decode("ascii")
        bobj["dts_frozen_digest"] = geometry_digest(bm)
        warnings.append(
            f"mesh {name!r} is {'sorted' if mesh.mesh_type == SORTED_MESH else 'multi-matframe'}; "
            f"it re-exports verbatim and refuses to export if its geometry is edited"
        )
    elif mesh.num_frames > 1:
        _add_frame_shape_keys(mesh, bobj)
    return bobj


def _add_frame_shape_keys(mesh: Mesh, bobj) -> None:
    """Import vertex-animation frames as shape keys frame_001..frame_NNN
    (frame 0 is the Basis).  Frame animation itself rides the sequences'
    frame tracks (dts_object_anim)."""
    vpf = mesh.verts_per_frame
    if vpf <= 0 or len(mesh.verts) < mesh.num_frames * vpf:
        return
    bobj.shape_key_add(name="Basis")
    for f in range(1, mesh.num_frames):
        sk = bobj.shape_key_add(name=f"frame_{f:03d}", from_mix=False)
        for vi in range(min(vpf, len(bobj.data.vertices))):
            sk.data[vi].co = Vector(mesh.verts[f * vpf + vi])


def _parent_rigid(bobj, arm_obj, node_index, bone_name_by_node, node_mats) -> None:
    if node_index < 0 or node_index not in bone_name_by_node:
        bobj.parent = arm_obj
        return
    bone_name = bone_name_by_node[node_index]
    bobj.parent = arm_obj
    bobj.parent_type = "BONE"
    bobj.parent_bone = bone_name
    # bone parenting hangs the child off the bone *tail*; place the object at
    # the node's transform
    bone_mat = node_mats[node_index]
    tail_offset = Matrix.Translation(Vector((0.0, -BONE_LENGTH, 0.0)))
    bobj.matrix_parent_inverse = tail_offset
    bobj.matrix_world = arm_obj.matrix_world @ bone_mat


def _bind_skin(shape: Shape, mesh: Mesh, bobj, arm_obj, bone_name_by_node) -> None:
    groups = {}
    for local_bone, node in enumerate(mesh.node_index):
        bone_name = bone_name_by_node.get(node)
        if bone_name is None:
            continue
        groups[local_bone] = bobj.vertex_groups.new(name=bone_name)
    for vi, bi, w in zip(mesh.vertex_index, mesh.bone_index, mesh.weight):
        g = groups.get(bi)
        if g is not None and vi < len(bobj.data.vertices):
            g.add([vi], w, "ADD")
    mod = bobj.modifiers.new(name="Armature", type="ARMATURE")
    mod.object = arm_obj
    bobj.parent = arm_obj


def _subshape_of_object(shape: Shape, obj_index: int) -> int:
    for s in range(len(shape.sub_shape_first_object)):
        first = shape.sub_shape_first_object[s]
        if first <= obj_index < first + shape.sub_shape_num_objects[s]:
            return s
    return 0


def _detail_for(shape: Shape, subshape: int, object_detail_num: int):
    for d in shape.details:
        if d.sub_shape_num == subshape and d.object_detail_num == object_detail_num:
            return d
    # collision details sometimes live on their own subshape; fall back to any
    # detail with the right objectDetailNum
    for d in shape.details:
        if d.object_detail_num == object_detail_num:
            return d
    return None

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
- Multi-frame meshes import as shape keys; sorted meshes record how their
  cluster tree should be rebuilt on export (dts_sorted_mode).
- Decals import as projector empties, or as a copy of the faces they cover
  when "Import Decals as Meshes" is on.  A DECAL_MESH in an object's own slots
  is skipped either way; the decal table is what import walks.
- Detail levels are organized into collections named after the detail.
"""

from __future__ import annotations

from pathlib import Path

import bpy
from mathutils import Matrix, Vector

from ..dtslib import Shape
from ..props.mesh import SCHEMA_VERSION as MESH_SCHEMA_VERSION
from ..props.shape import SCHEMA_VERSION
from ..dtslib.types import (
    DECAL_MESH,
    MESH_BILLBOARD,
    MESH_BILLBOARD_Z_AXIS,
    MESH_HAS_DETAIL_TEXTURE,
    MESH_USE_ENCODED_NORMALS,
    PRIM_FAN,
    PRIM_MATERIAL_MASK,
    PRIM_NO_MATERIAL,
    PRIM_STRIP,
    PRIM_TYPE_MASK,
    SKIN_MESH,
    SORTED_MESH,
    Mesh,
)

from . import matframes
from .decals import apply_default_states, import_decal_meshes, import_decals
from .materials import is_translucent, material_to_blender, reset_texture_cache
from .naming import object_display_name
from .nla import scene_fps, stack_actions
from .sequences import dts_local_matrix, import_sequences
from .visibility import (
    apply_default_vis,
    animated_object_names,
    fractional_object_names,
    wire_drivers,
    wire_fade_materials,
)

BONE_LENGTH = 0.25


def shape_to_blender(
    shape: Shape,
    name: str,
    context,
    filepath: str | None = None,
    do_import_sequences: bool = True,
    create_materials: bool = True,
    decals_as_meshes: bool = False,
    do_import_details: bool = True,
) -> tuple[bpy.types.Object, list[str]]:
    """Build the scene; returns (armature object, warnings)."""
    warnings = []
    scene_coll = context.scene.collection

    arm_obj = _build_armature(shape, name, context)
    scene_coll.objects.link(arm_obj)
    context.view_layer.objects.active = arm_obj
    _fill_armature_bones(shape, arm_obj)
    _store_node_transforms(shape, arm_obj)

    bone_name_by_node = {
        int(b["dts_node_index"]): b.name for b in arm_obj.data.bones if "dts_node_index" in b
    }

    bmats = []
    if create_materials:
        search_dir = Path(filepath).parent if filepath else None
        reset_texture_cache()
        bmats = [
            material_to_blender(m, i, shape.materials, search_dir, warnings)
            for i, m in enumerate(shape.materials)
        ]

    detail_collections = {}
    detail_sizes = {}
    node_mats = _node_armature_matrices(shape)
    # (object index, mesh slot) -> (blender object, dtslib mesh), so a decal can
    # find the target whose vertices its indices point at
    decal_targets = {}
    collection_by_object = {}
    kept_slots = None if do_import_details else _slots_to_import(shape)
    skipped_details = set()

    for obj_index, obj in enumerate(shape.objects):
        base_name = shape.name(obj.name_index)
        subshape = _subshape_of_object(shape, obj_index)
        for j in range(obj.num_meshes):
            mesh = shape.meshes[obj.start_mesh_index + j]
            if mesh is None:
                continue
            if mesh.mesh_type == DECAL_MESH:
                # a decal owned by an object slot rather than the decal table;
                # import_decals walks shape.decals instead
                continue
            detail = _detail_for(shape, subshape, j)
            if (
                kept_slots is not None
                and detail is not None
                and (detail.sub_shape_num, detail.object_detail_num) not in kept_slots
            ):
                # a detail with no geometry is legal, and the detail table
                # itself is stored on the armature, so the level still exists
                # in the exported file -- it is the triangles that are gone
                skipped_details.add(shape.name(detail.name_index))
                continue
            size = int(detail.size) if detail else j
            display = object_display_name(base_name or "object", size)
            bobj = _build_mesh_object(shape, mesh, display, bmats, warnings)
            bobj["dts_object_name"] = base_name
            # an object can own trailing empty mesh slots (a detail level it has
            # no geometry for); without the source count they get truncated
            bobj["dts_object_num_meshes"] = obj.num_meshes
            # export order would otherwise follow scene iteration order, which
            # sorts objects whose only geometry sits at a high detail level
            # (collision/LOS) to the end
            bobj["dts_object_index"] = obj_index
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
                # which frame / material frame the object rests on by default
                if st.frame_index:
                    bobj["dts_default_frame"] = st.frame_index
                if st.mat_frame_index:
                    bobj["dts_default_matframe"] = st.mat_frame_index

            coll_name = f"{name}.{shape.name(detail.name_index) if detail else f'detail{j}'}"
            coll = detail_collections.get(coll_name)
            if coll is None:
                coll = bpy.data.collections.new(coll_name)
                scene_coll.children.link(coll)
                detail_collections[coll_name] = coll
            detail_sizes[coll_name] = max(detail_sizes.get(coll_name, size), size)
            coll.objects.link(bobj)

            if mesh.mesh_type == SKIN_MESH:
                _bind_skin(shape, mesh, bobj, arm_obj, bone_name_by_node)
            else:
                _parent_rigid(bobj, arm_obj, obj.node_index, bone_name_by_node, node_mats)

            decal_targets[(obj_index, j)] = (bobj, mesh)
            collection_by_object[bobj.name] = coll

    _hide_non_default_details(context, detail_collections, detail_sizes)

    # the name table order is load-bearing: every name_index in the file is an
    # offset into it, and the source order (details, then nodes, then objects)
    # is not the order the exporter would rebuild it in
    _store_shape_tables(arm_obj, shape)
    _store_material_order(arm_obj, bmats)
    arm_obj["dts_smallest_visible_size"] = shape.smallest_visible_size
    arm_obj["dts_smallest_visible_dl"] = shape.smallest_visible_dl
    arm_obj["dts_exporter_version"] = shape.exporter_version

    if skipped_details:
        warnings.append(
            f"detail levels: {len(skipped_details)} level(s) not imported "
            f"({', '.join(sorted(skipped_details))}); their geometry is gone "
            f"from anything exported from this scene"
        )

    if shape.decals and decals_as_meshes:
        n_decals, n_meshes = import_decal_meshes(
            shape,
            arm_obj,
            bmats,
            decal_targets,
            lambda obj: collection_by_object.get(obj.name),
            _parent_like,
            warnings,
        )
        apply_default_states(arm_obj, shape)
        warnings.append(
            f"decals: {n_decals} imported as {n_meshes} mesh object(s).  This is "
            f"the file's own face list, which the projector form cannot "
            f"reproduce -- and it is not exported: a decal is exported from a "
            f"projector empty, and these have none"
        )
    elif shape.decals:
        n_decals, n_targets = import_decals(
            shape,
            arm_obj,
            bmats,
            decal_targets,
            lambda obj: collection_by_object.get(obj.name),
            _parent_like,
            warnings,
        )
        # a decal's rest state is per decal — most rest at -1 (off) and a
        # Damage sequence switches them on, but a wreck's rest at 0.  They must
        # exist before import_sequences keyframes them.
        apply_default_states(arm_obj, shape)
        warnings.append(
            f"decals: {n_decals} projector(s) covering {n_targets} mesh(es)"
        )

    if do_import_sequences and shape.sequences:
        actions = import_sequences(shape, arm_obj, bone_name_by_node)
        # one driver per mesh built from an animated object — a DTS object is
        # one Blender mesh per detail level, so this fans out across LODs
        names = animated_object_names(actions)
        if names:
            apply_default_vis(arm_obj, names)
            wired = wire_drivers(arm_obj, names, warnings)
            # a binary track is a swap the hide drivers already cover; only a
            # real fade needs the material to read object alpha
            fades = fractional_object_names(actions)
            mats = wire_fade_materials(fades) if fades else 0
            warnings.append(
                f"visibility: {len(names)} object(s) driven across {wired} mesh(es); "
                f"{len(fades)} fade via alpha ({mats} material(s) rewired)"
            )
        if shape.decals:
            # re-seed the states here, after the sequences have created their
            # fcurves: a driver whose target property has not been written since
            # the fcurve appeared never gets a relation to it and evaluates
            # stale forever.  The drivers themselves live on the decal branches
            # in the target materials, wired when the projector was built.
            apply_default_states(arm_obj, shape)
        _, skipped = stack_actions(arm_obj, actions, scene_fps(context))
        for action in skipped:
            warnings.append(
                f"sequence {action.name!r} has no channels this armature can evaluate "
                "(decal-only); no NLA strip created"
            )

    return arm_obj, warnings


def _store_shape_tables(arm_obj, shape: Shape) -> None:
    """The shape-wide tables, as editable collections on the armature.

    All four used to be JSON strings.  They are lists of records, which is what
    a CollectionProperty is for; ui/panels.py puts a UIList on each.
    """
    props = arm_obj.dts_shape
    props.is_shape = True
    props.schema_version = SCHEMA_VERSION

    # the name table order is load-bearing: every name_index in the file is an
    # offset into it, and the source order (details, then nodes, then objects)
    # is not the order the exporter would rebuild it in
    props.names.clear()
    for name in shape.names:
        props.names.add().name = name

    # details can exist with no geometry at all (an empty collision level), so
    # the table is kept rather than derived from the objects.  The trailing
    # error/poly-count fields are LOD selection metadata the add-on cannot
    # recompute, so they ride along.
    props.details.clear()
    for detail in shape.details:
        item = props.details.add()
        item.name = shape.name(detail.name_index)
        item.sub_shape_num = detail.sub_shape_num
        item.object_detail_num = detail.object_detail_num
        item.size = detail.size
        item.average_error = detail.average_error
        item.max_error = detail.max_error
        item.poly_count = detail.poly_count

    props.ifl_materials.clear()
    for ifl in shape.ifl_materials:
        item = props.ifl_materials.add()
        item.name = shape.name(ifl.raw[0])
        item.material_slot = ifl.raw[1]
        item.first_frame = ifl.raw[2]
        item.first_frame_off_time = ifl.raw[3]
        item.num_frames = ifl.raw[4]


def _store_material_order(arm_obj, bmats) -> None:
    """Material list order, as real pointers.

    Map slots and IFL entries index into this list, so unused materials have to
    survive too.  Pointers rather than names because names are not unique in
    real shapes -- 104 of the 630 corpus files reuse one.
    """
    props = arm_obj.dts_shape
    props.material_order.clear()
    for bmat in bmats:
        props.material_order.add().material = bmat


def _parent_like(bobj, target_obj, keep_transform: bool = False) -> None:
    """Hang a decal object off whatever its target hangs off.

    A decal's vertices are copied from the target's local coordinates, so it
    needs the target's transform verbatim; the projector empty, whose matrix
    was already solved in that space, keeps its own.  Both must follow the
    same bone or the decal slides off during animation.
    """
    world = bobj.matrix_world.copy() if keep_transform else target_obj.matrix_world.copy()
    bobj.parent = target_obj.parent
    bobj.parent_type = target_obj.parent_type
    bobj.parent_bone = target_obj.parent_bone
    bobj.matrix_parent_inverse = target_obj.matrix_parent_inverse.copy()
    for mod in target_obj.modifiers:
        if mod.type == "ARMATURE":
            copy = bobj.modifiers.new(mod.name, "ARMATURE")
            copy.object = mod.object
    bobj.matrix_world = world


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


def _store_node_transforms(shape: Shape, arm_obj) -> None:
    """The quantized rest transform, on the bone it describes.

    A quaternion and its negation are the same rotation, and the bone-matrix
    round trip picks a sign freely, so re-deriving these rewrites bits without
    changing the pose.  Export prefers them while the bone still agrees.
    """
    for bone in arm_obj.data.bones:
        index = bone.get("dts_node_index")
        if index is None or not 0 <= int(index) < len(shape.default_rotations):
            continue
        q = shape.default_rotations[int(index)]
        bone.dts_node.use_stored = True
        bone.dts_node.stored_rotation = (q.x, q.y, q.z, q.w)
        bone.dts_node.stored_translation = tuple(shape.default_translations[int(index)])


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


# every defined bit of the mesh flags word, as a named property.  The low three
# bits are the mesh type echoed into the word (types.py:33); whether an
# exporter echoed it varies per mesh -- all 119 sorted meshes in the corpus do,
# 16 of 20 skins do -- so it is recorded rather than inferred.
_MESH_FLAG_PROPS = (
    ("billboard", MESH_BILLBOARD),
    ("billboard_z", MESH_BILLBOARD_Z_AXIS),
    ("has_detail_texture", MESH_HAS_DETAIL_TEXTURE),
    ("use_encoded_normals", MESH_USE_ENCODED_NORMALS),
)

_MESH_TYPE_MASK = 0x7


def _store_mesh_flags(bobj, mesh: Mesh, warnings) -> None:
    props = bobj.dts_mesh
    props.is_dts = True
    props.schema_version = MESH_SCHEMA_VERSION
    for prop, bit in _MESH_FLAG_PROPS:
        setattr(props, prop, bool(mesh.flags & bit))
    props.echo_type_bits = bool(mesh.flags & _MESH_TYPE_MASK)
    known = _MESH_TYPE_MASK
    for _, bit in _MESH_FLAG_PROPS:
        known |= bit
    leftover = mesh.flags & ~known & 0xFFFFFFFF
    if leftover:
        # no corpus mesh has one; say so rather than inventing a home for it
        warnings.append(
            f"mesh {bobj.name!r} has undocumented flag bits {leftover:#010x}; they are dropped"
        )


def flags_from_blender(bobj, mesh_type: int) -> int:
    """Repack the mesh flags word from its named properties, for export.

    Lives here next to ``_store_mesh_flags`` so the two directions of the same
    table cannot drift apart -- the same reason materials.py keeps its flag
    pair together.
    """
    props = bobj.dts_mesh
    flags = 0
    for prop, bit in _MESH_FLAG_PROPS:
        if getattr(props, prop):
            flags |= bit
    if props.echo_type_bits:
        flags |= mesh_type & _MESH_TYPE_MASK
    return flags


def _store_merge_indices(bobj, mesh: Mesh, warnings) -> None:
    """Keep the legacy LOD-morph table as an int array on the object.

    Order matters and entries repeat, so a vertex group cannot hold it.  DTS
    vertex index and Blender vertex index agree here because the importer
    builds one Blender vertex per DTS vertex; the exporter maps back through
    its corner dedup.
    """
    if not mesh.merge_indices:
        return
    limit = len(bobj.data.vertices)
    kept = [i for i in mesh.merge_indices if 0 <= i < limit]
    if len(kept) != len(mesh.merge_indices):
        warnings.append(
            f"mesh {bobj.name!r}: {len(mesh.merge_indices) - len(kept)} merge index(es) "
            f"point past the mesh's {limit} vertices; dropped"
        )
    if kept:
        bobj["dts_merge_indices"] = kept


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
    _store_mesh_flags(bobj, mesh, warnings)
    _store_merge_indices(bobj, mesh, warnings)
    matframes.store(bm, mesh, warnings)
    _store_sorted_mode(bobj, mesh)
    if mesh.num_frames > 1:
        _add_frame_shape_keys(mesh, bobj)
    return bobj


def _store_sorted_mode(bobj, mesh: Mesh) -> None:
    """How this mesh's cluster tree should be rebuilt on export.

    A sorted mesh's BSP tables used to be the one thing that could not be
    re-derived, so the mesh carried a pickled copy of itself and refused to be
    edited.  dtslib/sorted_build.py generates them instead, which leaves only
    the *choice* to record -- and most of the time not even that.

    Export promotes any mesh on a translucent material to a sorted one, so for
    a translucent sorted mesh the mode is already implied and recording it
    would be a second source for one value.  352 of the corpus's 367 sorted
    meshes are translucent, so this stores nothing for almost all of them.
    The 15 that are not translucent still need the choice written down.

    The consequence, which is the point rather than a wrinkle: making such a
    mesh's material opaque makes it a standard mesh again, because the only
    reason it was sorted was that it blended.
    """
    if mesh.mesh_type != SORTED_MESH:
        return
    if not is_translucent(bobj):
        bobj.dts_mesh.sorted_mode = "BSP"
    if mesh.sorted_data is not None and mesh.sorted_data.always_write_depth:
        bobj.dts_mesh.always_write_depth = True


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


def _hide_non_default_details(context, detail_collections, detail_sizes) -> None:
    """Show only the default detail level.

    A shape carries every LOD plus collision/LOS details (which use negative
    sizes), all at the same origin — drawing them at once stacks dozens of
    overlapping copies.  The engine's default is the largest size, so keep that
    one visible and switch the rest off in the view layer.  Nothing is deleted,
    and hide_render is untouched.
    """
    if not detail_collections:
        return
    default = max(detail_collections, key=lambda cname: detail_sizes[cname])
    layer_children = {lc.collection.name: lc for lc in context.view_layer.layer_collection.children}
    for cname, coll in detail_collections.items():
        if cname == default:
            continue
        lc = layer_children.get(coll.name)
        if lc is not None:
            lc.hide_viewport = True
        else:  # collection not in this view layer (rare); fall back to the datablock
            coll.hide_viewport = True


def _slots_to_import(shape: Shape) -> set[tuple[int, int]]:
    """The (subshape, objectDetailNum) slots to build with LODs switched off.

    The engine's default is the largest size, so that is the level worth
    having; the rest are the same shape with fewer triangles standing at the
    same origin, which is what makes a full import of `light_male` eleven
    overlapping copies.

    Negative sizes are kept regardless.  A detail sized -1 is a collision or
    LOS hull rather than a level of detail, and dropping it would quietly turn
    a re-exported shape into one the engine cannot collide with -- a much
    larger loss than the one the option is asking for.
    """
    kept = set()
    for sub in {d.sub_shape_num for d in shape.details}:
        mine = [d for d in shape.details if d.sub_shape_num == sub]
        visible = [d for d in mine if d.size >= 0]
        if visible:
            kept.add((sub, max(visible, key=lambda d: d.size).object_detail_num))
        kept |= {(sub, d.object_detail_num) for d in mine if d.size < 0}
    return kept


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

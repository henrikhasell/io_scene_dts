"""Build a dtslib.Shape from a Blender scene (the reverse of shape_to_blender).

Geometry policy: meshes are triangulated and emitted as plain indexed
Triangles primitives, one per material (what the engine's own mdlExportDts
does).  Vertices are deduplicated on (position index, UV, split normal) since
DTS uses a single index stream.

Rigid mesh vertices are stored in node-local space; skin vertices in shape
space with initialTransforms = inverse of each bone's rest matrix.
"""

from __future__ import annotations

import base64
import json
import math
import pickle

import bpy
from mathutils import Matrix, Vector

from ..dtslib import Shape
from ..dtslib.primitives import MAX_TS_SET_SIZE
from ..dtslib.types import (
    MESH_BILLBOARD,
    MESH_BILLBOARD_Z_AXIS,
    PRIM_INDEXED,
    PRIM_NO_MATERIAL,
    PRIM_TRIANGLES,
    Detail,
    Mesh,
    Node,
    Object,
    ObjectState,
    Primitive,
    SKIN_MESH,
    STANDARD_MESH,
)

from .materials import material_from_blender
from .naming import detail_name_for_size, split_detail_suffix, strip_blender_dedup
from .sequences import blender_quat_to_dts, export_sequences


class ExportError(Exception):
    pass


def blender_to_shape(
    context,
    arm_obj: bpy.types.Object,
    selected_only: bool = False,
    do_export_sequences: bool = True,
) -> tuple[Shape, list[str]]:
    if arm_obj is None or arm_obj.type != "ARMATURE":
        raise ExportError("select an armature (the DTS shape root)")

    reset_material_cache()
    warnings: list[str] = []
    shape = Shape()

    # -- nodes --------------------------------------------------------
    bones = _ordered_bones(arm_obj)
    if len(bones) > MAX_TS_SET_SIZE:
        raise ExportError(f"{len(bones)} bones exceed the DTS limit of {MAX_TS_SET_SIZE} nodes")
    node_index_by_bone: dict[str, int] = {}
    node_arm_matrix: list[Matrix] = []
    for i, bone in enumerate(bones):
        node_index_by_bone[bone.name] = i
    for bone in bones:
        i = node_index_by_bone[bone.name]
        parent = node_index_by_bone[bone.parent.name] if bone.parent else -1
        dts_name = bone.get("dts_name") or strip_blender_dedup(bone.name)
        shape.nodes.append(Node(name_index=shape.add_name(dts_name), parent_index=parent))
        arm_mat = bone.matrix_local.copy()
        node_arm_matrix.append(arm_mat)
        local = (bones[parent].matrix_local.inverted() @ arm_mat) if parent >= 0 else arm_mat
        shape.default_rotations.append(blender_quat_to_dts(local.to_quaternion()))
        shape.default_translations.append(tuple(local.to_translation()))

    # -- gather mesh objects -----------------------------------------
    mesh_objs = _gather_mesh_objects(context, arm_obj, selected_only)
    if not mesh_objs:
        warnings.append("no mesh objects found under the armature")

    # group by (subshape, object base name) preserving discovery order; each
    # object's meshes are keyed by detail identity (name, size) — size alone
    # is ambiguous (e.g. "collision-1" and "detail-1" are both size -1)
    grouped: dict[tuple[int, str], dict[tuple[str, int], bpy.types.Object]] = {}
    order: list[tuple[int, str]] = []
    detail_odn_hint: dict[tuple[int, str, int], int] = {}  # (sub, name, size) -> stored odn
    for bobj in mesh_objs:
        base = bobj.get("dts_object_name") or None
        size = bobj.get("dts_detail_size")
        if base is None or size is None:
            parsed_base, parsed_size = split_detail_suffix(strip_blender_dedup(bobj.name))
            base = base or parsed_base
            size = size if size is not None else (parsed_size if parsed_size is not None else 2)
        size = int(size)
        detail_name = str(bobj.get("dts_detail_name") or detail_name_for_size(size))
        sub = int(bobj.get("dts_subshape", 0))
        key = (sub, str(base))
        dkey = (detail_name, size)
        if key not in grouped:
            grouped[key] = {}
            order.append(key)
        if dkey in grouped[key]:
            warnings.append(f"duplicate detail {detail_name!r} for object {base!r}; {bobj.name!r} skipped")
            continue
        grouped[key][dkey] = bobj
        if "dts_detail_odn" in bobj:
            detail_odn_hint[(sub, detail_name, size)] = int(bobj["dts_detail_odn"])

    # the armature may carry the full imported detail table — details can
    # exist with no geometry at all (e.g. an empty collision detail)
    stored_details = json.loads(arm_obj.get("dts_details", "[]") or "[]")

    subshapes = sorted({k[0] for k in order} | {int(d[1]) for d in stored_details}) or [0]
    sub_remap = {s: i for i, s in enumerate(subshapes)}

    # -- details ------------------------------------------------------
    # per remapped subshape: detail key (name, int size) -> objectDetailNum
    details_by_sub: dict[int, dict[tuple[str, int], int]] = {}
    detail_float_size: dict[tuple[int, str, int], float] = {}
    for dname, dsub, dodn, dsize in stored_details:
        s = sub_remap[int(dsub)]
        dkey = (str(dname), int(dsize))
        details_by_sub.setdefault(s, {})[dkey] = int(dodn)
        detail_float_size[(s, *dkey)] = float(dsize)
    for (sub, _), by_dkey in grouped.items():
        details_by_sub.setdefault(sub_remap[sub], {})
        for dkey in by_dkey:
            details_by_sub[sub_remap[sub]].setdefault(dkey, -1)
    for s, dmap in details_by_sub.items():
        orig_sub = subshapes[s]
        unassigned = [dk for dk, odn in dmap.items() if odn < 0]
        used = {odn for odn in dmap.values() if odn >= 0}
        for dk in sorted(unassigned, key=lambda k: (-k[1], k[0])):
            hint = detail_odn_hint.get((orig_sub, dk[0], dk[1]))
            if hint is not None and hint not in used:
                dmap[dk] = hint
            else:
                dmap[dk] = max(used, default=-1) + 1
            used.add(dmap[dk])

    detail_entries = []  # (size, name, subshape, odn)
    for s, dmap in sorted(details_by_sub.items()):
        for (dname, size), odn in dmap.items():
            detail_entries.append((detail_float_size.get((s, dname, size), float(size)), dname, s, odn))
    detail_entries.sort(key=lambda e: (-e[0], e[3]))
    for size, dname, s, odn in detail_entries:
        shape.details.append(
            Detail(
                name_index=shape.add_name(dname),
                sub_shape_num=s,
                object_detail_num=odn,
                size=float(size),
            )
        )

    # -- objects + meshes ---------------------------------------------
    object_index_by_name: dict[str, int] = {}
    per_sub_objects: dict[int, list[tuple[str, dict[int, bpy.types.Object]]]] = {}
    for key in order:
        sub = sub_remap[key[0]]
        per_sub_objects.setdefault(sub, []).append((key[1], grouped[key]))

    shape.sub_shape_first_node = [0] + [len(shape.nodes)] * (len(subshapes) - 1)
    shape.sub_shape_num_nodes = [len(shape.nodes)] + [0] * (len(subshapes) - 1)
    shape.sub_shape_first_decal = [0] * len(subshapes)
    shape.sub_shape_num_decals = [0] * len(subshapes)
    shape.sub_shape_first_object = []
    shape.sub_shape_num_objects = []

    for s in range(len(subshapes)):
        shape.sub_shape_first_object.append(len(shape.objects))
        dmap = details_by_sub.get(s, {})
        for base, by_dkey in per_sub_objects.get(s, []):
            start_mesh_index = len(shape.meshes)
            odn_map = {}
            for dkey, bobj in by_dkey.items():
                odn_map[dmap[dkey]] = bobj
            max_odn = max(odn_map) if odn_map else -1
            num_meshes = max_odn + 1
            node_index = -1
            for odn in range(num_meshes):
                bobj = odn_map.get(odn)
                if bobj is None:
                    shape.meshes.append(None)
                    continue
                mesh, mesh_node = _export_mesh(
                    shape, bobj, arm_obj, node_index_by_bone, node_arm_matrix, warnings
                )
                shape.meshes.append(mesh)
                if node_index < 0:
                    node_index = mesh_node
            obj = Object(
                name_index=shape.add_name(base),
                num_meshes=num_meshes,
                start_mesh_index=start_mesh_index,
                node_index=node_index,
            )
            object_index_by_name[base] = len(shape.objects)
            shape.objects.append(obj)
        shape.sub_shape_num_objects.append(len(shape.objects) - shape.sub_shape_first_object[-1])

    if len(shape.objects) > MAX_TS_SET_SIZE:
        raise ExportError(f"{len(shape.objects)} objects exceed the DTS limit of {MAX_TS_SET_SIZE}")

    # default object states, one per object
    for i, obj in enumerate(shape.objects):
        vis = 1.0
        for (sub, base), by_size in grouped.items():
            if object_index_by_name.get(base) == i:
                for bobj in by_size.values():
                    if "dts_default_vis" in bobj:
                        vis = float(bobj["dts_default_vis"])
        shape.object_states.append(ObjectState(vis, 0, 0))

    # -- materials ----------------------------------------------------
    shape.materials = [material_from_blender(bmat, i) for i, bmat in enumerate(_used_materials)]

    # -- bounds -------------------------------------------------------
    _compute_shape_bounds(shape, node_arm_matrix)

    # -- header-ish ---------------------------------------------------
    visible_sizes = sorted((d.size for d in shape.details if d.size >= 0))
    shape.smallest_visible_size = float(
        arm_obj.get("dts_smallest_visible_size", visible_sizes[0] if visible_sizes else 0.0)
    )
    if "dts_smallest_visible_dl" in arm_obj:
        shape.smallest_visible_dl = int(arm_obj["dts_smallest_visible_dl"])
    else:
        smallest = None
        for i, d in enumerate(shape.details):
            if d.size >= 0 and (smallest is None or d.size < shape.details[smallest].size):
                smallest = i
        shape.smallest_visible_dl = smallest if smallest is not None else 0
    shape.exporter_version = int(arm_obj.get("dts_exporter_version", shape.exporter_version))

    # -- sequences ----------------------------------------------------
    if do_export_sequences:
        actions = [a for a in bpy.data.actions if a.get("dts_sequence") or _action_targets_armature(a, arm_obj)]
        warnings += export_sequences(shape, arm_obj, actions, node_index_by_bone, object_index_by_name)

    return shape, warnings


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

_used_materials: list = []


def _ordered_bones(arm_obj) -> list:
    bones = list(arm_obj.data.bones)
    if bones and all("dts_node_index" in b for b in bones):
        bones.sort(key=lambda b: int(b["dts_node_index"]))
        return bones
    # hierarchy order: parents before children, stable otherwise
    ordered, seen = [], set()

    def visit(b):
        if b.name in seen:
            return
        if b.parent and b.parent.name not in seen:
            visit(b.parent)
        seen.add(b.name)
        ordered.append(b)

    for b in bones:
        visit(b)
    return ordered


def _gather_mesh_objects(context, arm_obj, selected_only):
    objs = []
    pool = context.selected_objects if selected_only else context.scene.objects
    for o in pool:
        if o.type != "MESH":
            continue
        if o.parent == arm_obj or any(
            m.type == "ARMATURE" and m.object == arm_obj for m in o.modifiers
        ):
            objs.append(o)
    return objs


def _action_targets_armature(action, arm_obj) -> bool:
    from .sequences import _iter_fcurves

    bone_names = {b.name for b in arm_obj.data.bones}
    for fc in _iter_fcurves(action):
        if fc.data_path.startswith('pose.bones["') and fc.data_path.split('"')[1] in bone_names:
            return True
    return False


def _material_slot_index(bmat) -> int:
    for i, m in enumerate(_used_materials):
        if m is bmat:
            return i
    _used_materials.append(bmat)
    return len(_used_materials) - 1


def _export_mesh(shape, bobj, arm_obj, node_index_by_bone, node_arm_matrix, warnings):
    """Returns (dtslib.Mesh, node_index)."""
    if "dts_frozen_payload" in bobj:
        mesh = pickle.loads(base64.b64decode(bobj["dts_frozen_payload"]))
        for slot in bobj.material_slots:
            if slot.material is not None:
                _material_slot_index(slot.material)
        return mesh, int(bobj.get("dts_node_index", -1))

    is_skin = bool(bobj.vertex_groups) and any(
        m.type == "ARMATURE" and m.object == arm_obj for m in bobj.modifiers
    )

    node_index = int(bobj.get("dts_node_index", -1))
    if not is_skin:
        if bobj.parent_type == "BONE" and bobj.parent_bone in node_index_by_bone:
            node_index = node_index_by_bone[bobj.parent_bone]
        if node_index < 0:
            node_index = 0 if node_arm_matrix else -1

    # vertex transform into DTS space
    arm_world_inv = arm_obj.matrix_world.inverted()
    if is_skin:
        to_dts = arm_world_inv @ bobj.matrix_world  # shape space
    else:
        node_mat = node_arm_matrix[node_index] if 0 <= node_index < len(node_arm_matrix) else Matrix.Identity(4)
        to_dts = node_mat.inverted() @ arm_world_inv @ bobj.matrix_world  # node space
    normal_mat = to_dts.to_3x3().inverted_safe().transposed()

    me = bobj.data
    me.calc_loop_triangles()
    uv_layer = me.uv_layers.active

    # dedup corners into DTS verts
    key_to_index = {}
    verts, norms, tverts = [], [], []
    corner_index = {}
    for tri in me.loop_triangles:
        for loop_index in tri.loops:
            loop = me.loops[loop_index]
            vi = loop.vertex_index
            uv = tuple(uv_layer.data[loop_index].uv) if uv_layer else (0.0, 0.0)
            normal = tuple(loop.normal if hasattr(loop, "normal") else me.vertices[vi].normal)
            key = (vi, round(uv[0], 6), round(uv[1], 6), round(normal[0], 4), round(normal[1], 4), round(normal[2], 4))
            idx = key_to_index.get(key)
            if idx is None:
                idx = len(verts)
                key_to_index[key] = idx
                verts.append(tuple(to_dts @ me.vertices[vi].co))
                n = (normal_mat @ Vector(normal))
                n.normalize()
                norms.append(tuple(n))
                tverts.append((uv[0], 1.0 - uv[1]))
            corner_index[loop_index] = idx

    if len(verts) > 0xFFFF:
        raise ExportError(f"mesh {bobj.name!r} has {len(verts)} unique vertices (DTS max 65535)")

    # triangles grouped per material; DTS winding is the reverse of Blender's
    by_mat: dict[int, list[int]] = {}
    for tri in me.loop_triangles:
        slot = tri.material_index if tri.material_index < len(bobj.material_slots) else 0
        bmat = bobj.material_slots[slot].material if bobj.material_slots else None
        mat_index = _material_slot_index(bmat) if bmat is not None else -1
        by_mat.setdefault(mat_index, []).extend(corner_index[li] for li in reversed(tri.loops))

    mesh = Mesh(mesh_type=SKIN_MESH if is_skin else STANDARD_MESH)
    mesh.verts = verts
    mesh.norms = norms
    mesh.tverts = tverts
    for mat_index, indices in by_mat.items():
        word = (
            (PRIM_TRIANGLES | PRIM_INDEXED | PRIM_NO_MATERIAL)
            if mat_index < 0
            else (PRIM_TRIANGLES | PRIM_INDEXED | (mat_index & 0x0FFFFFFF))
        )
        mesh.primitives.append(Primitive(len(mesh.indices), len(indices), word))
        mesh.indices.extend(indices)
    mesh.verts_per_frame = len(verts)
    mesh.flags = (MESH_BILLBOARD if bobj.get("dts_billboard") else 0) | (
        MESH_BILLBOARD_Z_AXIS if bobj.get("dts_billboard_z") else 0
    )
    _compute_mesh_bounds(mesh)

    if is_skin:
        _export_skin_data(shape, mesh, bobj, node_index_by_bone, node_arm_matrix, key_to_index, warnings)
        node_index = -1

    return mesh, node_index


def _export_skin_data(shape, mesh, bobj, node_index_by_bone, node_arm_matrix, key_to_index, warnings):
    me = bobj.data
    group_to_node = {}
    for g in bobj.vertex_groups:
        node = node_index_by_bone.get(g.name)
        if node is None:
            warnings.append(f"skin {bobj.name!r}: vertex group {g.name!r} has no bone; ignored")
        group_to_node[g.index] = node

    used_nodes = []
    node_to_local = {}

    # per original blender vertex: weights
    weights_by_bvert = {}
    for v in me.vertices:
        entries = []
        for ge in v.groups:
            node = group_to_node.get(ge.group)
            if node is None or ge.weight <= 0.0:
                continue
            entries.append((node, ge.weight))
        total = sum(w for _, w in entries)
        if not entries or total <= 0.0:
            entries = [(0, 1.0)]
            total = 1.0
        weights_by_bvert[v.index] = [(n, w / total) for n, w in entries]

    for key, dts_index in key_to_index.items():
        bvert = key[0]
        for node, w in weights_by_bvert.get(bvert, [(0, 1.0)]):
            if node not in node_to_local:
                node_to_local[node] = len(used_nodes)
                used_nodes.append(node)
            mesh.vertex_index.append(dts_index)
            mesh.bone_index.append(node_to_local[node])
            mesh.weight.append(w)

    mesh.node_index = used_nodes
    mesh.initial_verts = list(mesh.verts)
    mesh.initial_norms = list(mesh.norms)
    for node in used_nodes:
        inv = node_arm_matrix[node].inverted()
        mesh.initial_transforms.append(tuple(v for row in inv for v in row))


def _compute_mesh_bounds(mesh):
    if not mesh.verts:
        mesh.bounds = (0.0,) * 6
        mesh.center = (0.0, 0.0, 0.0)
        mesh.radius_int = 0
        return
    xs, ys, zs = zip(*mesh.verts)
    mn = (min(xs), min(ys), min(zs))
    mx = (max(xs), max(ys), max(zs))
    mesh.bounds = mn + mx
    center = tuple((a + b) / 2.0 for a, b in zip(mn, mx))
    mesh.center = center
    mesh.radius_int = int(
        max(math.dist(center, v) for v in mesh.verts)
    )


def _compute_shape_bounds(shape, node_arm_matrix):
    points = []
    for obj in shape.objects:
        for j in range(obj.num_meshes):
            mesh = shape.meshes[obj.start_mesh_index + j]
            if mesh is None:
                continue
            verts = mesh.verts or mesh.initial_verts
            if mesh.mesh_type == SKIN_MESH or obj.node_index < 0:
                mat = Matrix.Identity(4)
            else:
                mat = node_arm_matrix[obj.node_index] if obj.node_index < len(node_arm_matrix) else Matrix.Identity(4)
            points.extend(tuple(mat @ Vector(v)) for v in verts)
    if not points:
        shape.bounds = (0.0,) * 6
        shape.center = (0.0, 0.0, 0.0)
        shape.radius = shape.tube_radius = 0.0
        return
    xs, ys, zs = zip(*points)
    mn, mx = (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))
    shape.bounds = mn + mx
    center = tuple((a + b) / 2.0 for a, b in zip(mn, mx))
    shape.center = center
    shape.radius = max(math.dist(center, p) for p in points)
    shape.tube_radius = max(
        math.hypot(p[0] - center[0], p[1] - center[1]) for p in points
    )


def reset_material_cache():
    _used_materials.clear()

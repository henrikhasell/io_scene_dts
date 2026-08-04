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
from ..dtslib.runtime_links import recompute_runtime_links
from ..dtslib.types import (
    MESH_BILLBOARD,
    MESH_BILLBOARD_Z_AXIS,
    PRIM_INDEXED,
    PRIM_NO_MATERIAL,
    PRIM_TRIANGLES,
    Decal,
    Detail,
    IflMaterial,
    Mesh,
    Node,
    Object,
    ObjectState,
    Primitive,
    Quat16,
    SKIN_MESH,
    SORTED_MESH,
    STANDARD_MESH,
)

from .materials import materials_from_blender
from .naming import detail_name_for_size, split_detail_suffix, strip_blender_dedup
from .sequences import blender_quat_to_dts, export_sequences
from .shape_to_blender import geometry_digest


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

    # seed the name table in its original order so every add_name() below
    # resolves to the source index; names for anything added in Blender still
    # append at the end
    stored_names = json.loads(arm_obj.get("dts_names_order", "[]") or "[]")
    shape.names = [str(n) for n in stored_names]

    # -- nodes --------------------------------------------------------
    bones = _ordered_bones(arm_obj)
    if len(bones) > MAX_TS_SET_SIZE:
        raise ExportError(f"{len(bones)} bones exceed the DTS limit of {MAX_TS_SET_SIZE} nodes")
    stored_transforms = json.loads(arm_obj.get("dts_node_transforms", "{}") or "{}")
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
        rot = blender_quat_to_dts(local.to_quaternion())
        trans = tuple(local.to_translation())
        stored = stored_transforms.get(dts_name)
        if stored is not None:
            rot, trans = _prefer_stored_transform(rot, trans, stored)
        shape.default_rotations.append(rot)
        shape.default_translations.append(trans)

    # -- gather mesh objects -----------------------------------------
    mesh_objs = _gather_mesh_objects(context, arm_obj, selected_only)
    if not mesh_objs:
        warnings.append("no mesh objects found under the armature")

    # seed the material list in the original order — map slots and IFL
    # entries index into it, so unused materials must survive too
    stored_mat_order = json.loads(arm_obj.get("dts_materials_order", "[]") or "[]")
    if stored_mat_order:
        pool = {}
        for o in mesh_objs:
            for slot in o.material_slots:
                if slot.material is not None and "dts_name" in slot.material:
                    pool.setdefault(str(slot.material["dts_name"]).lower(), slot.material)
        for bm in bpy.data.materials:
            if "dts_name" in bm:
                pool.setdefault(str(bm["dts_name"]).lower(), bm)
        # prefer the recorded index: two entries can share a name
        by_index = {}
        for bm in bpy.data.materials:
            if "dts_material_index" in bm:
                by_index.setdefault(int(bm["dts_material_index"]), bm)
        for slot_index, mat_name in enumerate(stored_mat_order):
            bm = by_index.get(slot_index) or pool.get(str(mat_name).lower())
            if bm is not None:
                _material_slot_index(bm)
            else:
                warnings.append(
                    f"material {mat_name!r} from the original list no longer exists; "
                    f"material indices may shift"
                )

    # group by (subshape, object base name) preserving discovery order; each
    # object's meshes are keyed by detail identity (name, size) — size alone
    # is ambiguous (e.g. "collision-1" and "detail-1" are both size -1)
    grouped: dict[tuple[int, str], dict[tuple[str, int], bpy.types.Object]] = {}
    order: list[tuple[int, str]] = []
    source_order: dict[tuple[int, str], int] = {}  # key -> index in the source shape
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
            if "dts_object_index" in bobj:
                source_order[key] = int(bobj["dts_object_index"])
        if dkey in grouped[key]:
            warnings.append(f"duplicate detail {detail_name!r} for object {base!r}; {bobj.name!r} skipped")
            continue
        grouped[key][dkey] = bobj
        if "dts_detail_odn" in bobj:
            detail_odn_hint[(sub, detail_name, size)] = int(bobj["dts_detail_odn"])

    # imported objects go back in their original order; anything added in
    # Blender keeps discovery order and follows them
    if source_order:
        discovery = {k: i for i, k in enumerate(order)}
        order.sort(key=lambda k: (0, source_order[k]) if k in source_order else (1, discovery[k]))

    # the armature may carry the full imported detail table — details can
    # exist with no geometry at all (e.g. an empty collision detail)
    stored_details = json.loads(arm_obj.get("dts_details", "[]") or "[]")

    subshapes = sorted({k[0] for k in order} | {int(d[1]) for d in stored_details}) or [0]
    sub_remap = {s: i for i, s in enumerate(subshapes)}

    # -- details ------------------------------------------------------
    # per remapped subshape: detail key (name, int size) -> objectDetailNum
    details_by_sub: dict[int, dict[tuple[str, int], int]] = {}
    detail_float_size: dict[tuple[int, str, int], float] = {}
    # (sub, name, size) -> (average_error, max_error, poly_count)
    detail_metrics: dict[tuple[int, str, int], tuple[float, float, int]] = {}
    for entry in stored_details:
        # earlier versions stored only the first four fields
        dname, dsub, dodn, dsize = entry[:4]
        s = sub_remap[int(dsub)]
        dkey = (str(dname), int(dsize))
        details_by_sub.setdefault(s, {})[dkey] = int(dodn)
        detail_float_size[(s, *dkey)] = float(dsize)
        if len(entry) >= 7:
            detail_metrics[(s, *dkey)] = (float(entry[4]), float(entry[5]), int(entry[6]))
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
        metrics = detail_metrics.get((s, dname, int(size)))
        detail = Detail(
            name_index=shape.add_name(dname),
            sub_shape_num=s,
            object_detail_num=odn,
            size=float(size),
        )
        if metrics is not None:
            detail.average_error, detail.max_error, detail.poly_count = metrics
        shape.details.append(detail)

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

    # source mesh index -> index in the shape being written, for meshes
    # re-emitted verbatim; drives the parent_mesh fixup below
    mesh_src_to_new: dict[int, int] = {}
    verbatim_meshes: set[int] = set()

    for s in range(len(subshapes)):
        shape.sub_shape_first_object.append(len(shape.objects))
        dmap = details_by_sub.get(s, {})
        for base, by_dkey in per_sub_objects.get(s, []):
            start_mesh_index = len(shape.meshes)
            odn_map = {}
            for dkey, bobj in by_dkey.items():
                odn_map[dmap[dkey]] = bobj
            max_odn = max(odn_map) if odn_map else -1
            # keep any trailing null slots the source object declared -- they are
            # detail levels this object simply has no geometry for
            stored_counts = [
                int(b["dts_object_num_meshes"])
                for b in by_dkey.values()
                if "dts_object_num_meshes" in b
            ]
            num_meshes = max([max_odn + 1] + stored_counts)
            node_index = -1
            for odn in range(num_meshes):
                bobj = odn_map.get(odn)
                if bobj is None:
                    shape.meshes.append(None)
                    continue
                mesh, mesh_node, verbatim = _export_mesh(
                    shape, bobj, arm_obj, node_index_by_bone, node_arm_matrix, warnings
                )
                if verbatim:
                    src = bobj.get("dts_source_index")
                    if src is not None:
                        mesh_src_to_new[int(src)] = len(shape.meshes)
                    verbatim_meshes.add(len(shape.meshes))
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
        vis, frame, matframe = 1.0, 0, 0
        for (sub, base), by_size in grouped.items():
            if object_index_by_name.get(base) == i:
                for bobj in by_size.values():
                    if "dts_default_vis" in bobj:
                        vis = float(bobj["dts_default_vis"])
                    if "dts_default_frame" in bobj:
                        frame = int(bobj["dts_default_frame"])
                    if "dts_default_matframe" in bobj:
                        matframe = int(bobj["dts_default_matframe"])
        shape.object_states.append(ObjectState(vis, frame, matframe))

    # -- materials ----------------------------------------------------
    shape.materials, mat_warnings = materials_from_blender(_used_materials)
    warnings += mat_warnings

    # -- IFL materials (preserved shape-level) ------------------------
    for entry in json.loads(arm_obj.get("dts_ifl_materials", "[]") or "[]"):
        raw = list(entry["raw"])
        raw[0] = shape.add_name(str(entry["name"]))
        shape.ifl_materials.append(IflMaterial(tuple(raw)))

    # -- decals (preserved records + frozen mesh payloads) ------------
    decal_index_map = _restore_decals(shape, arm_obj, object_index_by_name, warnings)

    # every mesh is in shape.meshes by now, so parent_mesh can be resolved
    _remap_parent_meshes(shape, mesh_src_to_new, verbatim_meshes)

    # engine scratch links, derivable from the hierarchy we just built
    recompute_runtime_links(shape)

    # -- bounds -------------------------------------------------------
    # always recomputed: these are derived from the geometry, so carrying the
    # source values through would just risk them going stale
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
        warnings += export_sequences(
            shape, arm_obj, actions, node_index_by_bone, object_index_by_name, decal_index_map
        )

    return shape, warnings


_QUAT_LSB_SLACK = 2  # Quat16 components are int16; a matrix round-trip drifts a little
_TRANS_EPS = 1e-5


def _prefer_stored_transform(rot, trans, stored):
    """Keep the source rest transform unless the bone genuinely moved.

    Deriving these from the bone matrix is lossy in two harmless ways: the
    quaternion sign is arbitrary (q and -q are the same rotation) and the
    int16 components drift by an LSB or two.  Both rewrite the file without
    moving anything, so the stored values win when the bone still matches them.
    """
    (sx, sy, sz, sw), strans = stored
    stored_rot = Quat16(int(sx), int(sy), int(sz), int(sw))
    got = (rot.x, rot.y, rot.z, rot.w)
    same = all(
        abs(a - b) <= _QUAT_LSB_SLACK for a, b in zip(got, (sx, sy, sz, sw))
    ) or all(abs(a + b) <= _QUAT_LSB_SLACK for a, b in zip(got, (sx, sy, sz, sw)))
    if not same:
        return rot, trans
    if any(abs(a - b) > _TRANS_EPS for a, b in zip(trans, strans)):
        return stored_rot, trans
    return stored_rot, tuple(float(v) for v in strans)


def _remap_parent_meshes(shape, src_to_new: dict[int, int], verbatim: set[int]) -> None:
    """Re-point parent_mesh at the re-emitted parent, or clear it.

    A verbatim payload carries the *source* file's parent_mesh index, which
    means nothing in the new mesh layout -- leaving it there is what produced
    files the reader rejected with "mesh references bad parent".

    Clearing is always safe: the reader materializes a child's verts/tverts/
    norms as a prefix slice of its parent's, so every Mesh we hold is
    self-contained.  Remapping is preferred where possible because it keeps the
    vertex sharing, and with it the file size.
    """
    for new_index, mesh in enumerate(shape.meshes):
        if mesh is None or mesh.parent_mesh < 0:
            continue
        parent_new = src_to_new.get(mesh.parent_mesh, -1)
        parent = shape.meshes[parent_new] if 0 <= parent_new < len(shape.meshes) else None
        shareable = (
            new_index in verbatim
            and parent is not None
            and parent_new < new_index
            and parent_new in verbatim
            # the engine slices the parent's arrays with the child's counts, so
            # the parent must still be at least as long in every shared array
            and len(parent.verts) >= len(mesh.verts)
            and len(parent.tverts) >= len(mesh.tverts)
            and len(parent.norms) >= len(mesh.norms)
        )
        mesh.parent_mesh = parent_new if shareable else -1


def _restore_decals(shape, arm_obj, object_index_by_name, warnings) -> dict[int, int]:
    """Re-emit preserved decal records/meshes; returns old->new index map."""
    stored = json.loads(arm_obj.get("dts_decals", "[]") or "[]")
    default_states = json.loads(arm_obj.get("dts_decal_states", "[]") or "[]")
    if not stored:
        return {}

    # order decals by the (remapped) subshape of their owner object so the
    # subShapeFirstDecal ranges stay contiguous
    def owner_subshape(obj_index: int) -> int:
        for s in range(len(shape.sub_shape_first_object)):
            first = shape.sub_shape_first_object[s]
            if first <= obj_index < first + shape.sub_shape_num_objects[s]:
                return s
        return 0

    placed = []  # (subshape, old_index, entry, obj_index)
    for old_index, entry in enumerate(stored):
        obj_index = object_index_by_name.get(str(entry["object"]))
        if obj_index is None:
            warnings.append(
                f"decal {entry['name']!r}: owner object {entry['object']!r} was not "
                f"exported; decal dropped"
            )
            continue
        placed.append((owner_subshape(obj_index), old_index, entry, obj_index))
    placed.sort(key=lambda p: (p[0], p[1]))

    decal_index_map: dict[int, int] = {}
    shape.sub_shape_first_decal = [0] * len(shape.sub_shape_first_object)
    shape.sub_shape_num_decals = [0] * len(shape.sub_shape_first_object)
    next_first = 0
    for s in range(len(shape.sub_shape_first_object)):
        shape.sub_shape_first_decal[s] = next_first
        group = [p for p in placed if p[0] == s]
        shape.sub_shape_num_decals[s] = len(group)
        next_first += len(group)

    for _, old_index, entry, obj_index in placed:
        start = len(shape.meshes)
        for slot in entry["meshes"]:
            shape.meshes.append(pickle.loads(base64.b64decode(slot)) if slot else None)
        decal_index_map[old_index] = len(shape.decals)
        shape.decals.append(
            Decal((shape.add_name(str(entry["name"])), len(entry["meshes"]), start, obj_index, -1))
        )
        shape.decal_states.append(
            int(default_states[old_index]) if old_index < len(default_states) else 0
        )
    return decal_index_map


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
    """Returns (dtslib.Mesh, node_index, verbatim)."""
    # "dts_frozen_payload" is the pre-source-payload property name; a .blend
    # saved by an older add-on only has the strict sorted/multi-matframe ones
    payload = bobj.get("dts_source_payload") or bobj.get("dts_frozen_payload")
    if payload:
        stored_digest = bobj.get("dts_source_digest") or bobj.get("dts_frozen_digest")
        edited = bool(stored_digest) and geometry_digest(bobj.data) != stored_digest
        strict = bool(bobj.get("dts_strict_freeze")) or "dts_source_payload" not in bobj
        if edited and strict:
            kind = "sorted" if int(bobj.get("dts_mesh_type", 0)) == SORTED_MESH else "multi-matframe"
            raise ExportError(
                f"{bobj.name!r} is a {kind} mesh that only round-trips verbatim, but its "
                f"geometry has been edited — revert the edits or delete the object"
            )
        if not edited:
            mesh = pickle.loads(base64.b64decode(payload))
            for slot in bobj.material_slots:
                if slot.material is not None:
                    _material_slot_index(slot.material)
            return mesh, int(bobj.get("dts_node_index", -1)), True

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

    # vertex-animation frames from shape keys named frame_NNN
    frame_keys = []
    if me.shape_keys is not None:
        frame_keys = sorted(
            (kb for kb in me.shape_keys.key_blocks if kb.name.startswith("frame_")),
            key=lambda kb: kb.name,
        )
    if frame_keys:
        if is_skin:
            warnings.append(f"{bobj.name!r}: frame_* shape keys on a skin are not supported; ignored")
        else:
            mesh.num_frames = 1 + len(frame_keys)
            base_norms = list(mesh.norms)
            for kb in frame_keys:
                for key in key_to_index:  # ordered by insertion == DTS vert order
                    bvert = key[0]
                    mesh.verts.append(tuple(to_dts @ kb.data[bvert].co))
                mesh.norms.extend(base_norms)

    _compute_mesh_bounds(mesh)

    if is_skin:
        _export_skin_data(shape, mesh, bobj, node_index_by_bone, node_arm_matrix, key_to_index, warnings)
        node_index = -1

    return mesh, node_index, False


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

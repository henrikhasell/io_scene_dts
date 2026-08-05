"""Build a dtslib.Shape from a Blender scene (the reverse of shape_to_blender).

Geometry policy: meshes are triangulated and emitted as plain indexed
Triangles primitives, one per material (what the engine's own mdlExportDts
does).  Vertices are deduplicated on (position index, UV, split normal) since
DTS uses a single index stream.

Rigid mesh vertices are stored in node-local space; skin vertices in shape
space with initialTransforms = inverse of each bone's rest matrix.
"""

from __future__ import annotations

import json
import math

import bpy
from mathutils import Matrix, Vector

from ..dtslib import Shape
from ..dtslib.primitives import MAX_TS_SET_SIZE
from ..dtslib.runtime_links import recompute_runtime_links
from ..dtslib.sorted_build import build_sorted, flat_sorted
from ..dtslib.types import (
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

from . import matframes
from .decals import blender_lookup_of, build_decals
from .materials import materials_from_blender
from .naming import detail_name_for_size, split_detail_suffix, strip_blender_dedup
from .sequences import blender_quat_to_dts, export_sequences
from .shape_to_blender import flags_from_blender
from .vertex_pool import VertexPool


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

    # target Blender object name -> Blender position to DTS index, for decals
    decal_target_lookups: dict[str, dict] = {}

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
            node_index, obj_lookups = _export_object_meshes(
                shape, base, odn_map, num_meshes, arm_obj, node_index_by_bone,
                node_arm_matrix, warnings,
            )
            decal_target_lookups.update(obj_lookups)
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

    # -- decals (recomputed from the projector empties) ---------------
    # before the material list is built: a decal's material is often used by
    # nothing else, so this is where it gets registered
    decal_index_map = build_decals(
        shape, arm_obj, object_index_by_name, _material_slot_index, warnings,
        target_lookups=decal_target_lookups,
    )

    # -- materials ----------------------------------------------------
    shape.materials, mat_warnings = materials_from_blender(_used_materials)
    warnings += mat_warnings

    # -- IFL materials (preserved shape-level) ------------------------
    for entry in json.loads(arm_obj.get("dts_ifl_materials", "[]") or "[]"):
        raw = list(entry["raw"])
        raw[0] = shape.add_name(str(entry["name"]))
        shape.ifl_materials.append(IflMaterial(tuple(raw)))


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
    """Mesh objects belonging to this shape, excluding decals.

    A decal mesh hangs off the armature exactly like its target does — that is
    what keeps it stuck to the right bone during animation — so it looks like
    an ordinary shape mesh here.  It is not one: build_decals emits it into the
    decal table instead, and letting it through as well exports every decal
    twice, once as a phantom object with its own geometry and detail levels.
    """
    objs = []
    pool = context.selected_objects if selected_only else context.scene.objects
    for o in pool:
        if o.type != "MESH" or "dts_decal_name" in o:
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


def _export_object_meshes(
    shape, base, odn_map, num_meshes, arm_obj, node_index_by_bone, node_arm_matrix, warnings
):
    """Append one DTS object's detail levels to ``shape.meshes``.

    The levels are built *lowest detail first* against one VertexPool, so each
    one occupies a prefix of the shared array and can name the largest level as
    its ``parent_mesh`` instead of storing vertices of its own.  That is worth
    x1.85 in file size and is the main thing the pickled payload used to buy.
    Emission order is unchanged -- slot 0 first -- because a parent has to
    precede its children in the mesh table for the reader to have seen it
    (dtslib/reader.py:251-253).

    Returns (node index, {target Blender object name: decal vertex lookup}).
    """
    pool = VertexPool()
    built = {}
    lookups = {}
    # descending: the largest level seals last and so ends up holding the whole
    # pool, which makes it the one every other level can point at
    for odn in sorted(odn_map, reverse=True):
        built[odn] = _export_mesh(
            shape, odn_map[odn], arm_obj, node_index_by_bone, node_arm_matrix,
            warnings, pool=pool,
        )

    # the parent is the smallest slot that used the pool; it sealed last, so
    # its prefix is the whole pool and every other pooled prefix fits inside it
    pooled = [odn for odn, entry in built.items() if entry[3] is not None]
    parent_odn = min(pooled) if pooled else None

    mesh_index_of = {}
    node_index = -1
    for odn in range(num_meshes):
        entry = built.get(odn)
        if entry is None:
            shape.meshes.append(None)
            continue
        mesh, mesh_node, _verbatim, pool_length, bvert_to_dts = entry
        if (
            parent_odn is not None
            and odn != parent_odn
            and pool_length is not None
            # a multi-frame mesh's array runs past the shared prefix into its
            # frame blocks, so it can hold a prefix for others but cannot be
            # one itself
            and mesh.num_frames == 1
        ):
            mesh.parent_mesh = mesh_index_of[parent_odn]
        mesh_index_of[odn] = len(shape.meshes)
        shape.meshes.append(mesh)
        if bvert_to_dts:
            lookups[odn_map[odn].name] = blender_lookup_of(odn_map[odn], bvert_to_dts)
        if node_index < 0:
            node_index = mesh_node
    return node_index, lookups


def _export_mesh(shape, bobj, arm_obj, node_index_by_bone, node_arm_matrix, warnings, pool=None):
    """Returns (dtslib.Mesh, node_index, verbatim, pool_length, bvert_to_dts).

    ``pool`` shares one vertex array across the detail levels of an object; see
    mapping/vertex_pool.py.  ``pool_length`` is the prefix this mesh occupies,
    or None when it did not use the pool (a skin, or a replayed payload).
    ``bvert_to_dts`` maps Blender vertex index to DTS vertex index, which the
    decal exporter needs to match faces without going through float drift.
    """
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

    # A skin's vertices are in shape space, not this object's node space, so
    # they cannot share a pool with the rigid meshes of the same object; and
    # skin sharing would additionally need initial_verts, vertex_index,
    # bone_index, weight and node_index to be prefixes too (mesh_io.py:107-140).
    pooled = pool is not None and not is_skin
    store = pool if pooled else VertexPool()

    # Two coincident vertices must stay distinct when something outside the
    # (position, uv, normal) triple tells them apart: a shape key can move them
    # in different directions, and a skin can weight them to different bones.
    frame_keys = _frame_shape_keys(me)
    split_vertices = is_skin or bool(frame_keys)

    # dedup corners into DTS verts
    corner_index = {}
    blender_vert_of = {}
    dts_index_of_bvert = {}
    for tri in me.loop_triangles:
        for loop_index in tri.loops:
            loop = me.loops[loop_index]
            vi = loop.vertex_index
            uv = tuple(uv_layer.data[loop_index].uv) if uv_layer else (0.0, 0.0)
            normal = tuple(loop.normal if hasattr(loop, "normal") else me.vertices[vi].normal)
            n = normal_mat @ Vector(normal)
            n.normalize()
            idx = store.intern(
                tuple(to_dts @ me.vertices[vi].co),
                (uv[0], 1.0 - uv[1]),
                tuple(n),
                split=vi if split_vertices else None,
            )
            corner_index[loop_index] = idx
            blender_vert_of.setdefault(idx, vi)
            dts_index_of_bvert.setdefault(vi, idx)

    pool_length = store.seal()
    verts = store.verts[:pool_length]
    tverts = store.tverts[:pool_length]
    norms = store.norms[:pool_length]

    if len(verts) > 0xFFFF:
        raise ExportError(f"mesh {bobj.name!r} has {len(verts)} unique vertices (DTS max 65535)")

    # triangles, in DTS winding (the reverse of Blender's), tagged with the
    # primitive word they belong under
    tris: list[tuple[int, int, int, int]] = []
    by_mat: dict[int, list[int]] = {}
    for tri in me.loop_triangles:
        slot = tri.material_index if tri.material_index < len(bobj.material_slots) else 0
        bmat = bobj.material_slots[slot].material if bobj.material_slots else None
        mat_index = _material_slot_index(bmat) if bmat is not None else -1
        word = (
            (PRIM_TRIANGLES | PRIM_INDEXED | PRIM_NO_MATERIAL)
            if mat_index < 0
            else (PRIM_TRIANGLES | PRIM_INDEXED | (mat_index & 0x0FFFFFFF))
        )
        a, b, c = (corner_index[li] for li in reversed(tri.loops))
        tris.append((a, b, c, word))
        by_mat.setdefault(word, []).extend((a, b, c))

    mesh = Mesh(mesh_type=SKIN_MESH if is_skin else STANDARD_MESH)
    mesh.verts = verts
    mesh.norms = norms
    mesh.tverts = tverts

    sort_mode = _sorted_mode(bobj, is_skin, bool(frame_keys), warnings)
    if sort_mode == "NONE":
        for word, indices in by_mat.items():
            mesh.primitives.append(Primitive(len(mesh.indices), len(indices), word))
            mesh.indices.extend(indices)
    else:
        mesh.mesh_type = SORTED_MESH
        mat_frames = matframes.frame_count(me)
        if sort_mode == "BSP":
            mesh.primitives, mesh.indices, mesh.sorted_data = build_sorted(
                verts, tris,
                depth=int(bobj.get("dts_sorted_depth", 2)),
                num_mat_frames=mat_frames,
                always_write_depth=int(bool(bobj.get("dts_always_write_depth"))),
            )
        else:  # FLAT: keep the mesh type, claim no ordering
            for word, indices in by_mat.items():
                mesh.primitives.append(Primitive(len(mesh.indices), len(indices), word))
                mesh.indices.extend(indices)
            mesh.sorted_data = flat_sorted(
                mesh.primitives, verts,
                num_mat_frames=mat_frames,
                always_write_depth=int(bool(bobj.get("dts_always_write_depth"))),
            )
    mesh.verts_per_frame = len(verts)
    mesh.flags = flags_from_blender(bobj, mesh.mesh_type)

    # DTS vertex index -> the Blender vertex it came from.  A pooled prefix can
    # hold entries interned by a *different* detail level of the same object,
    # which this mesh has no vertex for; those are -1.
    blender_vert_per_dts_vert = [blender_vert_of.get(i, -1) for i in range(len(verts))]

    # extra material frames append their own tvert blocks after frame 0's
    for block in matframes.extra_blocks(me, blender_vert_per_dts_vert):
        mesh.tverts.extend(block)
    mesh.num_mat_frames = matframes.frame_count(me)

    merge = bobj.get("dts_merge_indices")
    if merge:
        mesh.merge_indices = [
            dts_index_of_bvert[i] for i in merge if i in dts_index_of_bvert
        ]
        dropped = len(merge) - len(mesh.merge_indices)
        if dropped:
            # a source mesh packed as strips carries vertices that only ever
            # appear in a degenerate stitch triangle; re-deriving as triangle
            # lists has no use for them, so a merge entry naming one has
            # nothing left to point at
            warnings.append(
                f"mesh {bobj.name!r}: {dropped} of {len(merge)} merge indices name a vertex "
                f"no longer referenced by any face; dropped"
            )

    # vertex-animation frames from shape keys named frame_NNN
    if frame_keys:
        if is_skin:
            warnings.append(f"{bobj.name!r}: frame_* shape keys on a skin are not supported; ignored")
        else:
            mesh.num_frames = 1 + len(frame_keys)
            base_verts = list(mesh.verts)
            base_norms = list(mesh.norms)
            for kb in frame_keys:
                for dts_index, bvert in enumerate(blender_vert_per_dts_vert):
                    if bvert < 0:
                        # a shared-prefix entry this mesh does not own has no
                        # shape key to sample; hold it at its rest position,
                        # which is already in DTS space
                        mesh.verts.append(base_verts[dts_index])
                    else:
                        mesh.verts.append(tuple(to_dts @ kb.data[bvert].co))
                mesh.norms.extend(base_norms)

    _compute_mesh_bounds(mesh)

    if is_skin:
        _export_skin_data(
            shape, mesh, bobj, node_index_by_bone, node_arm_matrix,
            blender_vert_per_dts_vert, warnings,
        )
        node_index = -1

    return mesh, node_index, False, (pool_length if pooled else None), dts_index_of_bvert


def _sorted_mode(bobj, is_skin: bool, has_frames: bool, warnings) -> str:
    """Which sorted-mesh treatment this object asked for.

    Never inferred from the material.  Translucency is what sorted meshes are
    *for*, but turning every translucent mesh in a scene into one would change
    how the engine draws it without anybody asking.
    """
    mode = str(bobj.get("dts_sorted_mode", "NONE")).upper()
    if mode not in ("NONE", "FLAT", "BSP"):
        warnings.append(
            f"mesh {bobj.name!r}: unknown dts_sorted_mode {mode!r}; exporting as a standard mesh"
        )
        return "NONE"
    if mode == "NONE":
        return "NONE"
    # mesh_type is one field: a mesh is a skin or it is sorted, never both
    if is_skin:
        warnings.append(
            f"mesh {bobj.name!r} is skinned, so it cannot also be sorted; "
            f"exporting as a skin"
        )
        return "NONE"
    if has_frames:
        warnings.append(
            f"mesh {bobj.name!r} has vertex-animation frames, which the sorted "
            f"cluster tables do not address; exporting as a standard mesh"
        )
        return "NONE"
    return mode


def _frame_shape_keys(me):
    """Vertex-animation frames of a mesh, in frame order."""
    if me.shape_keys is None:
        return []
    return sorted(
        (kb for kb in me.shape_keys.key_blocks if kb.name.startswith("frame_")),
        key=lambda kb: kb.name,
    )


def _export_skin_data(
    shape, mesh, bobj, node_index_by_bone, node_arm_matrix, blender_vert_per_dts_vert, warnings
):
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

    for dts_index, bvert in enumerate(blender_vert_per_dts_vert):
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

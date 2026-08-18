"""Build a dtslib.Shape from a Blender scene (the reverse of shape_to_blender).

Geometry policy: meshes are triangulated and emitted as plain indexed
Triangles primitives, one per material (what the engine's own mdlExportDts
does).  Vertices are deduplicated on (position index, UV, split normal) since
DTS uses a single index stream.

Rigid mesh vertices are stored in node-local space; skin vertices in shape
space with initialTransforms = inverse of each bone's rest matrix.
"""

from __future__ import annotations

import contextlib
import math

import bpy
from mathutils import Matrix, Vector

from ..dtslib import Shape
from ..dtslib.primitives import MAX_TS_SET_SIZE
from ..dtslib.runtime_links import recompute_runtime_links
from ..dtslib.sorted_build import build_sorted, flat_sorted
from ..dtslib.translucency import shape_has_translucent_mesh
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

from ..props import migrate
from . import matframes
from .decals import bake_decals_as_objects, blender_lookup_of, build_decals
from .materials import ifl_materials_from_blender, is_translucent, materials_from_blender
from .naming import (
    detail_name_for_size,
    dts_object_and_size,
    split_detail_suffix,
    strip_blender_dedup,
)
from .sequences import blender_quat_to_dts, export_sequences
from .shape_to_blender import flags_from_blender
from .vertex_pool import VertexPool


class ExportError(Exception):
    pass


@contextlib.contextmanager
def flushed_edit_mode(context):
    """Leave Edit Mode for the duration of the block, then put it back.

    Edit Mode keeps the geometry in a BMesh, and the ``Mesh`` datablock's
    parallel arrays are not written until it is flushed.  A UV layer is the
    trap: it still *exists*, so an ``if uv_layer`` guard passes, while
    ``uv_layer.data`` reads as empty next to a full ``loops`` -- so the first
    corner exported raised ``IndexError`` rather than anything a user could act
    on.  ``create_decal`` drops out of Edit Mode before reading faces for the
    same reason.

    Restored in a ``finally``, because the mode belongs to the user and an
    export that fails halfway should not leave them somewhere they did not put
    themselves.  One ``mode_set`` covers multi-object editing in both
    directions: it exits for every object in the mode, and re-entering with the
    same active object brings back the others, which are still selected.
    """
    obj = context.object
    if obj is None or obj.mode != "EDIT":
        yield
        return
    bpy.ops.object.mode_set(mode="OBJECT")
    try:
        yield
    finally:
        if context.view_layer.objects.active is obj:
            bpy.ops.object.mode_set(mode="EDIT")


def blender_to_shape(
    context,
    arm_obj: bpy.types.Object,
    selected_only: bool = False,
    do_export_sequences: bool = True,
    combine_reflectance: bool = True,
    decals_as_meshes: bool = False,
) -> tuple[Shape, list, list[str]]:
    """The shape, the textures export has to write beside it, and warnings."""
    if arm_obj is None or arm_obj.type != "ARMATURE":
        raise ExportError("select an armature (the DTS shape root)")
    with flushed_edit_mode(context):
        return _blender_to_shape(
            context, arm_obj, selected_only, do_export_sequences, combine_reflectance,
            decals_as_meshes,
        )


def _blender_to_shape(
    context,
    arm_obj: bpy.types.Object,
    selected_only: bool,
    do_export_sequences: bool,
    combine_reflectance: bool = True,
    decals_as_meshes: bool = False,
) -> tuple[Shape, list, list[str]]:
    """The body, with Edit Mode already flushed by the wrapper above."""
    reset_material_cache()
    warnings: list[str] = []
    shape = Shape()

    # A scene from v1.2 or earlier converts on load, but the handler only fires
    # when a file is *opened* with the add-on already enabled.  Enable it
    # afterwards and the old keys are still there, unread -- which would export
    # a shape missing its name table, details and IFL entries without saying so.
    stale = migrate.legacy_keys_present()
    if stale:
        raise ExportError(
            f"this scene still holds data from an older version of the add-on "
            f"({', '.join(stale[:3])}{'...' if len(stale) > 3 else ''}). Run "
            f"Object > DTS > Convert DTS Data From an Older Version "
            f"(io_scene_dts.migrate_scene) first"
        )

    # seed the name table in its original order so every add_name() below
    # resolves to the source index; names for anything added in Blender still
    # append at the end
    shape.names = [item.name for item in arm_obj.dts_shape.names]

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
        rot = blender_quat_to_dts(local.to_quaternion())
        trans = tuple(local.to_translation())
        if bone.dts_node.use_stored:
            rot, trans = _prefer_stored_transform(
                rot, trans, (list(bone.dts_node.stored_rotation), list(bone.dts_node.stored_translation))
            )
        shape.default_rotations.append(rot)
        shape.default_translations.append(trans)

    # -- gather mesh objects -----------------------------------------
    mesh_objs = _gather_mesh_objects(context, arm_obj, selected_only)
    if not mesh_objs:
        warnings.append("no mesh objects found under the armature")

    # seed the material list in the original order — map slots and IFL
    # entries index into it, so unused materials must survive too
    # A real pointer per slot, so a deleted material shows up as an empty slot
    # rather than as a name that silently resolves to a different material --
    # names are not unique in real shapes.
    for slot_index, ref in enumerate(arm_obj.dts_shape.material_order):
        if ref.material is not None:
            _material_slot_index(ref.material)
        else:
            warnings.append(
                f"material slot {slot_index} of the original list is empty; "
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
        base, size = dts_object_and_size(bobj)
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

    # ...and then everything translucent moves to the end, whatever order the
    # source put it in.  Objects are drawn in list order and a blended surface
    # only composites correctly over what is already in the frame buffer, so an
    # opaque object drawn after one blends against the wrong background -- and
    # one drawn after a decal erases it.  The sort is stable, so it lifts the
    # opaque objects ahead rather than reshuffling either group, and it runs
    # per sub-shape for free because `per_sub_objects` buckets by sub-shape
    # below.  This is where an imported shape can come out ordered differently
    # from the file it was read from: 3 corpus shapes are, and all 3 have an
    # opaque object sitting behind a translucent one.
    translucent_keys = {
        key for key, by_dkey in grouped.items()
        if any(is_translucent(b) for b in by_dkey.values())
    }
    order.sort(key=lambda k: k in translucent_keys)

    # the armature may carry the full imported detail table — details can
    # exist with no geometry at all (e.g. an empty collision detail)
    stored_details = [
        (d.name, d.sub_shape_num, d.object_detail_num, d.size,
         d.average_error, d.max_error, d.poly_count)
        for d in arm_obj.dts_shape.details
    ]

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
    # DTS object name -> {detail slot: Blender object}, so a decal can reach
    # every detail level of its owner and not just the one it points at
    decal_slot_objects: dict[str, dict] = {}
    # when decals are baked: decal index -> (object index, rest visibility)
    baked_decals: dict[int, tuple[int, float]] = {}

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
            node_index, obj_lookups, obj_slots = _export_object_meshes(
                shape, base, odn_map, num_meshes, arm_obj, node_index_by_bone,
                node_arm_matrix, warnings,
            )
            decal_target_lookups.update(obj_lookups)
            decal_slot_objects[base] = obj_slots
            obj = Object(
                name_index=shape.add_name(base),
                num_meshes=num_meshes,
                start_mesh_index=start_mesh_index,
                node_index=node_index,
            )
            object_index_by_name[base] = len(shape.objects)
            shape.objects.append(obj)
        # inside the loop, before the count is taken: a baked decal is an object
        # of this subshape and the object ranges are contiguous
        if decals_as_meshes:
            baked_decals.update(
                bake_decals_as_objects(
                    shape, arm_obj, s, object_index_by_name, _material_slot_index,
                    lambda b: _dts_placement(b, arm_obj, node_index_by_bone, node_arm_matrix),
                    warnings, slot_objects=decal_slot_objects,
                )
            )
        shape.sub_shape_num_objects.append(len(shape.objects) - shape.sub_shape_first_object[-1])

    if len(shape.objects) > MAX_TS_SET_SIZE:
        raise ExportError(f"{len(shape.objects)} objects exceed the DTS limit of {MAX_TS_SET_SIZE}")

    # default object states, one per object
    baked_vis = {index: vis for index, vis in baked_decals.values()}
    for i, obj in enumerate(shape.objects):
        vis, frame, matframe = 1.0, 0, 0
        if i in baked_vis:
            # a baked decal is not in `grouped` -- it has no Blender mesh object
            # -- so its rest state comes from the decal's own, and the loop
            # below would leave it visible, which is wrong for the
            # overwhelming majority that rest off
            shape.object_states.append(ObjectState(baked_vis[i], 0, 0))
            continue
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
    decal_index_map = {}
    if decals_as_meshes:
        if baked_decals:
            warnings.append(
                f"decals: {len(baked_decals)} baked as mesh object(s).  The file "
                f"carries no decal entries, so re-importing gives ordinary meshes "
                f"and no projectors"
            )
    else:
        decal_index_map = build_decals(
            shape, arm_obj, object_index_by_name, _material_slot_index, warnings,
            target_lookups=decal_target_lookups,
            slot_objects=decal_slot_objects,
        )
    if not decal_index_map and not baked_decals:
        # a scene imported with "Import Decals as Meshes" has decals in it and
        # exports none, because nothing here reads a mesh.  Silence would make
        # that look like a shape that never had any
        orphans = [o for o in context.scene.objects if "dts_decal_name" in o]
        if orphans:
            warnings.append(
                f"{len(orphans)} decal mesh object(s) were not exported: a decal "
                f"is exported from its projector empty, and these have none.  "
                f"Run Migrate DTS Scene to turn them into projectors"
            )

    # -- materials ----------------------------------------------------
    shape.materials, texture_writes, mat_warnings = materials_from_blender(
        _used_materials, combine_reflectance
    )
    warnings += mat_warnings

    # -- IFL materials (derived from the materials that flip) ---------
    # In material-list order, because the sequences' ifl_matters bits are
    # positional: bit i means "the i'th entry of this table".
    ifl_materials, ifl_writes, ifl_warnings = ifl_materials_from_blender(
        shape, _used_materials
    )
    shape.ifl_materials = ifl_materials
    texture_writes += ifl_writes
    warnings += ifl_warnings

    # -- decals need something translucent to draw against --------------
    # Asked here rather than at build_decals because translucency is a
    # material-list flag and the list does not exist until the line above.
    # Refused rather than warned: unlike a version the engine forces on you,
    # this is fixable in Blender in one click, and a shape that writes and
    # reads perfectly while drawing its decals wrong is the kind of failure
    # nothing downstream can diagnose.
    if shape.decals and not shape_has_translucent_mesh(shape):
        raise ExportError(
            f"this shape has {len(shape.decals)} decal(s) and nothing translucent "
            f"to draw them against; the engine needs at least one blended mesh in "
            f"a shape that carries decals.  Set Render Method to Blended on the "
            f"decal's own material, or on a material of the mesh it sits on "
            f"(Material Properties > Settings).  Every one of the corpus's 153 "
            f"decal-bearing shapes does one or the other"
        )

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
        actions = _actions_for_armature(arm_obj)
        warnings += export_sequences(
            shape, arm_obj, actions, node_index_by_bone, object_index_by_name, decal_index_map,
            baked_decal_objects={i: obj for i, (obj, _vis) in baked_decals.items()},
        )

    return shape, texture_writes, warnings


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
    """Mesh objects belonging to this shape.

    A decal is an empty, so the ``dts_decal_name`` guard is not about the
    ordinary case.  It covers the two ways decal *meshes* can be in a scene:
    a .blend written by an older version whose ``props/migrate.py`` has not run
    yet, and an import made with ``Import Decals as Meshes``.  Both park meshes
    on the armature exactly like their targets, and letting one through exports
    it as a phantom object with its own geometry and detail levels.
    """
    def belongs(o):
        if o.type != "MESH" or "dts_decal_name" in o:
            return False
        return o.parent == arm_obj or any(
            m.type == "ARMATURE" and m.object == arm_obj for m in o.modifiers
        )

    if not selected_only:
        return [o for o in context.scene.objects if belongs(o)]

    chosen = [o for o in context.selected_objects if belongs(o)]
    # A hidden object cannot be selected, so "Selected Objects Only" would drop
    # every detail level the user had switched off in the viewport -- and
    # eleven LODs stand at the same origin, so hiding the small ones to see the
    # large one is the normal way to work.  The file came out with detail 128
    # and nothing else, silently, because the meshes were still *there*.
    #
    # Hiding is a display choice, so the hidden levels of an object that was
    # selected come too.  Only those: a mesh that is visible and simply not
    # selected is a choice, and still left out.
    picked = {id(o) for o in chosen}
    wanted = {dts_object_and_size(o)[0] for o in chosen}
    for o in context.scene.objects:
        if id(o) in picked or not belongs(o):
            continue
        try:
            visible = o.visible_get()
        except RuntimeError:
            visible = False  # not in this view layer at all, so unselectable
        if not visible and dts_object_and_size(o)[0] in wanted:
            chosen.append(o)
    return chosen


def _action_targets_armature(action, arm_obj) -> bool:
    from .sequences import _iter_fcurves

    bone_names = {b.name for b in arm_obj.data.bones}
    for fc in _iter_fcurves(action):
        if fc.data_path.startswith('pose.bones["') and fc.data_path.split('"')[1] in bone_names:
            return True
    return False


def _actions_owned_by(arm_obj) -> set:
    """The actions this armature's own animation data claims.

    Its assigned action plus every NLA strip's -- which is where the importer
    puts a shape's sequences (mapping/nla.py) and how a user says in Blender
    that an animation belongs to this rig.
    """
    owned = set()
    anim = arm_obj.animation_data
    if anim is None:
        return owned
    if anim.action is not None:
        owned.add(anim.action)
    for track in anim.nla_tracks:
        for strip in track.strips:
            if strip.action is not None:
                owned.add(strip.action)
    return owned


def _actions_for_armature(arm_obj) -> list:
    """The actions that are *this* shape's sequences.

    ``dts_sequence`` marks an action as a DTS sequence; it does not say whose,
    and testing it alone was a leak.  Exporting a weapon from a scene that also
    held an imported player wrote the *player's* 45 sequences into the weapon's
    file -- every channel dropped with a warning, because none of its bones
    exist on the weapon, leaving 45 empty sequences nobody asked for.

    Ownership decides instead.  The bone-name test stays as a fallback for an
    action that has not been stacked yet, but it cannot reach across to one
    another armature already claims: two shapes rigged to the same skeleton
    share bone names, so the fallback alone would leak between *them*.
    """
    owned = _actions_owned_by(arm_obj)
    claimed_elsewhere = set()
    for other in bpy.data.objects:
        if other is arm_obj or other.type != "ARMATURE":
            continue
        claimed_elsewhere |= _actions_owned_by(other)
    return [
        action
        for action in bpy.data.actions
        if action in owned
        or (action not in claimed_elsewhere and _action_targets_armature(action, arm_obj))
    ]


def _dts_material_key(bmat) -> str:
    """What the .dts will call this material, which is what makes two of them
    one entry.

    Not the datablock name: a DTS material *is* its texture filename, so two
    Blender materials that would write the same name cannot be two entries.
    That is what lets ``decals.private_material_for`` give each decal target its
    own copy of a material -- 17 copies of ``base.lmale``, one .dts entry.
    """
    return str(bmat.get("dts_name") or bmat.name).lower()


def _material_slot_index(bmat) -> int:
    key = _dts_material_key(bmat)
    for i, m in enumerate(_used_materials):
        if m is bmat or _dts_material_key(m) == key:
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

    Returns (node index, {target Blender object name: decal vertex lookup},
    {detail slot: Blender object}).  A decal covers every detail level of its
    owner, so it needs the slot map to find the other levels; the object it
    points at is only one of them.
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
    return node_index, lookups, dict(odn_map)


def _dts_placement(bobj, arm_obj, node_index_by_bone, node_arm_matrix):
    """Where a mesh object sits in the file: (is_skin, node, to_dts, normal_mat).

    Split out of :func:`_export_mesh` because the decal baker needs the same
    answer: a baked decal is geometry copied off its target and has to land in
    exactly the space the target's own vertices did, or it exports a few
    millimetres from the surface it is supposed to sit on.
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

    arm_world_inv = arm_obj.matrix_world.inverted()
    if is_skin:
        to_dts = arm_world_inv @ bobj.matrix_world  # shape space
    else:
        node_mat = node_arm_matrix[node_index] if 0 <= node_index < len(node_arm_matrix) else Matrix.Identity(4)
        to_dts = node_mat.inverted() @ arm_world_inv @ bobj.matrix_world  # node space
    return is_skin, node_index, to_dts, to_dts.to_3x3().inverted_safe().transposed()


def _export_mesh(shape, bobj, arm_obj, node_index_by_bone, node_arm_matrix, warnings, pool=None):
    """Returns (dtslib.Mesh, node_index, verbatim, pool_length, bvert_to_dts).

    ``pool`` shares one vertex array across the detail levels of an object; see
    mapping/vertex_pool.py.  ``pool_length`` is the prefix this mesh occupies,
    or None when it did not use the pool (a skin, or a replayed payload).
    ``bvert_to_dts`` maps Blender vertex index to DTS vertex index, which the
    decal exporter needs to match faces without going through float drift.
    """
    is_skin, node_index, to_dts, normal_mat = _dts_placement(
        bobj, arm_obj, node_index_by_bone, node_arm_matrix
    )

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
                depth=bobj.dts_mesh.sorted_depth,
                num_mat_frames=mat_frames,
                always_write_depth=int(bobj.dts_mesh.always_write_depth),
            )
        else:  # FLAT: keep the mesh type, claim no ordering
            for word, indices in by_mat.items():
                mesh.primitives.append(Primitive(len(mesh.indices), len(indices), word))
                mesh.indices.extend(indices)
            mesh.sorted_data = flat_sorted(
                mesh.primitives, verts,
                num_mat_frames=mat_frames,
                always_write_depth=int(bobj.dts_mesh.always_write_depth),
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
    """Which sorted-mesh treatment this mesh gets.

    A translucent mesh is promoted to a BSP-sorted one.  Sorting is what
    translucency needs -- without it the engine draws the triangles in storage
    order and a tree or a canopy blends back-to-front from some angles and not
    others -- and every one of the 367 sorted meshes in the corpus sits on a
    translucent material.

    The promotion is not free and it is not subtle: 5462 of the corpus's 34077
    standard meshes are on a translucent material, so this makes far more
    sorted meshes than the shipped art has, and it fires on re-export of an
    imported shape as well.  Setting the mode explicitly still wins, and
    `NONE` is not a way to opt out -- it is the value being promoted from.
    """
    mode = bobj.dts_mesh.sorted_mode
    promoted = mode == "NONE" and is_translucent(bobj)
    if promoted:
        mode = "BSP"
    if mode == "NONE":
        return "NONE"
    # mesh_type is one field: a mesh is a skin or it is sorted, never both.
    # A promotion that cannot happen is not worth a warning -- nobody asked
    # for it, and a translucent skin would produce one on every export.
    if is_skin:
        if not promoted:
            warnings.append(
                f"mesh {bobj.name!r} is skinned, so it cannot also be sorted; "
                f"exporting as a skin"
            )
        return "NONE"
    if has_frames:
        if not promoted:
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

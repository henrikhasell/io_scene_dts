"""Bring a scene saved by an older version of the add-on forward.

Everything consumed is deleted afterwards, both so the old and new forms cannot
disagree and so the export path can tell a stale scene from a converted one.

Idempotent, and keyed on ``schema_version``, so running it twice costs nothing.
It runs from a ``load_post`` handler and from ``io_scene_dts.migrate_scene`` for
the case where the add-on is enabled after the file is already open.

The parsing itself lives in ``props/legacy.py``, which imports no ``bpy``.
"""

from __future__ import annotations

import bpy

from .legacy import (
    LEGACY_ACTION_KEYS,
    LEGACY_MESH_KEYS,
    LEGACY_PAYLOAD_KEYS,
    LEGACY_SHAPE_KEYS,
    _loads,
    parse_details,
    parse_ground,
    parse_ifl,
    parse_node_transforms,
    parse_triggers,
)
from .decal import SCHEMA_VERSION as DECAL_SCHEMA_VERSION
from .material import LEGACY_COMBINE_KEY
from .material import SCHEMA_VERSION as MATERIAL_SCHEMA_VERSION
from .mesh import SCHEMA_VERSION as MESH_SCHEMA_VERSION
from .shape import SCHEMA_VERSION

__all__ = ["legacy_keys_present", "migrate_all", "on_load"]


# ----------------------------------------------------------------------
# applying (needs bpy)
# ----------------------------------------------------------------------


def _fill(collection, records) -> None:
    collection.clear()
    for record in records:
        item = collection.add()
        for field, value in record.items():
            setattr(item, field, value)


def _ifl_materials_in_order() -> list:
    """The materials an export would emit IFL entries for, in the same order.

    Entries are derived from the materials now, in material-list order, so a
    legacy index has to be resolved through that same ordering.  `material_order`
    is the only record of it that survives a .blend.
    """
    ordered = []
    for obj in bpy.data.objects:
        if obj.type == "ARMATURE" and obj.dts_shape.is_shape:
            ordered = [r.material for r in obj.dts_shape.material_order if r.material]
            if ordered:
                break
    if not ordered:
        ordered = [m for m in bpy.data.materials if "dts_name" in m]
    return [m for m in ordered if m.dts_material.is_ifl]


def _ifl_entries_to_materials(entries, report: list) -> None:
    """The shape's IFL table becomes a checkbox on each material it named.

    The table was a list of (name, material slot, three ints of uninitialised
    engine scratch).  Only the first two were ever data, and both are implied
    by the material: an IFL entry exists because a material flips, and its name
    is that material's name plus `.ifl`.  So the entry becomes `is_ifl`.

    The frame list cannot be recovered here -- it lives in the `.ifl` file, and
    migration has no path to look in.  Re-import the shape to get the frames.
    """
    if not entries:
        return
    by_index = {
        int(m["dts_material_index"]): m
        for m in bpy.data.materials
        if "dts_material_index" in m
    }
    claimed = 0
    for entry in entries:
        mat = by_index.get(int(entry.get("material_slot", -1)))
        if mat is None:
            mat = bpy.data.materials.get(str(entry.get("name", ""))[: -len(".ifl")])
        if mat is None:
            continue
        mat.dts_material.is_ifl = True
        claimed += 1
    report.append(
        f"{claimed} of {len(entries)} IFL entries became a checkbox on their "
        f"material; their frame lists are in the .ifl files and need a re-import"
    )


def _fill_ifl_matters(props, indices, report: list) -> None:
    """Legacy per-sequence IFL membership: indices into the old table."""
    props.ifl_matters.clear()
    ordered = _ifl_materials_in_order()
    for i in indices:
        item = props.ifl_matters.add()
        if 0 <= int(i) < len(ordered):
            item.material = ordered[int(i)]


def migrate_armature(obj, report: list) -> bool:
    props = obj.dts_shape
    present = [key for key in LEGACY_SHAPE_KEYS if key in obj]
    if not present:
        return False
    if props.schema_version >= SCHEMA_VERSION:
        # The tables are already the current form, so a legacy key beside them
        # is a leftover rather than the source of truth -- converting from it
        # would overwrite good data with older data.  Drop it and say so.
        for key in present:
            del obj[key]
        report.append(
            f"{obj.name}: discarded {len(present)} stale key(s) beside the converted "
            f"tables ({', '.join(present)})"
        )
        return True

    props.is_shape = True
    _fill(props.names, [{"name": n} for n in _loads(obj.get("dts_names_order"), [])])
    _fill(props.details, parse_details(obj.get("dts_details")))
    _ifl_entries_to_materials(parse_ifl(obj.get("dts_ifl_materials")), report)

    props.material_order.clear()
    by_index = {
        int(m["dts_material_index"]): m
        for m in bpy.data.materials
        if "dts_material_index" in m
    }
    for slot, name in enumerate(_loads(obj.get("dts_materials_order"), [])):
        ref = props.material_order.add()
        ref.material = by_index.get(slot) or bpy.data.materials.get(str(name))

    transforms = parse_node_transforms(obj.get("dts_node_transforms"))
    if obj.type == "ARMATURE":
        for bone in obj.data.bones:
            dts_name = str(bone.get("dts_name") or bone.name)
            entry = transforms.get(dts_name)
            if entry is None:
                continue
            bone.dts_node.use_stored = True
            bone.dts_node.stored_rotation = entry["stored_rotation"]
            bone.dts_node.stored_translation = entry["stored_translation"]

    for key in LEGACY_SHAPE_KEYS:
        if key in obj:
            del obj[key]
    props.schema_version = SCHEMA_VERSION
    report.append(f"{obj.name}: shape tables converted")
    return True


def migrate_action(action, report: list) -> bool:
    props = action.dts_sequence_props
    present = [key for key in LEGACY_ACTION_KEYS if key in action]
    if not present:
        props.schema_version = SCHEMA_VERSION
        return False
    if props.schema_version >= SCHEMA_VERSION:
        for key in present:
            del action[key]
        report.append(
            f"{action.name}: discarded {len(present)} stale key(s) beside the "
            f"converted tables"
        )
        return True

    _fill(props.ground, parse_ground(action.get("dts_ground")))
    _fill(props.triggers, parse_triggers(action.get("dts_triggers")))
    _fill_ifl_matters(props, _loads(action.get("dts_ifl_matters"), []), report)
    scale = _loads(action.get("dts_scale_anim"), {})
    if scale:
        flags = int(scale.get("flags", 0))
        props.scale_mode = (
            "UNIFORM" if flags & 1 else "ALIGNED" if flags & 2 else "ARBITRARY"
        )
        report.append(
            f"{action.name}: scale animation was a stored blob and is not "
            f"reconstructable as bone channels; re-import the shape to recover it"
        )

    dropped = [k for k in ("dts_object_anim", "dts_decal_anim") if k in action]
    if dropped:
        report.append(
            f"{action.name}: object/decal state tracks were stored beside the "
            f"curves; the curves are what export reads now"
        )
    for key in LEGACY_ACTION_KEYS:
        if key in action:
            del action[key]
    props.schema_version = SCHEMA_VERSION
    return True


def migrate_mesh_flags(obj, report: list) -> bool:
    """The per-mesh flags, from ID properties to the property group.

    The old form wrote a key only when the bit was *set*, so absence meant
    False -- which is why they could never be ticked in a panel.
    """
    present = [key for key in LEGACY_MESH_KEYS if key in obj]
    if not present:
        return False
    props = obj.dts_mesh
    if props.schema_version < MESH_SCHEMA_VERSION:
        props.is_dts = True
        for key in present:
            value = obj[key]
            field = key.removeprefix("dts_")
            if field in ("sorted_mode",):
                mode = str(value).upper()
                props.sorted_mode = mode if mode in ("NONE", "FLAT", "BSP") else "NONE"
            elif field == "sorted_depth":
                props.sorted_depth = int(value)
            else:
                setattr(props, field, bool(value))
        props.schema_version = MESH_SCHEMA_VERSION
    for key in present:
        del obj[key]
    return True


def migrate_meshes(report: list) -> bool:
    """Drop the pickled mesh payload without reading it.

    Unpickling it here would harvest what it holds -- strip packing, merge
    indices, material frames, cluster tables -- and would also put
    ``pickle.loads`` back on a path fed by whatever .blend is opened, which is
    the thing this whole change exists to remove.  The loss is recoverable by
    re-importing the .dts; the risk is not worth the convenience.
    """
    touched = 0
    for obj in bpy.data.objects:
        if any(key in obj for key in LEGACY_PAYLOAD_KEYS):
            for key in LEGACY_PAYLOAD_KEYS:
                if key in obj:
                    del obj[key]
            touched += 1
    if touched:
        report.append(
            f"{touched} mesh(es) carried a payload from io_scene_dts 1.2 or earlier. "
            f"It was discarded rather than unpickled; strip packing, merge indices, "
            f"material frames and sorted cluster tables are gone. Re-import the .dts "
            f"to recover them."
        )
    return bool(touched)


def migrate_decals(report: list) -> bool:
    """Decals: from one mesh object per detail level to just the empty.

    The old form kept the covered faces as a copied mesh, so the empty was only
    a handle on the projection.  Coverage is recomputed from the empty now, so
    the meshes are not data any more -- and leaving them would be worse than
    useless: they hang off the armature exactly like their targets and the
    exporter would emit each one again as a phantom object.

    The empty keeps the decal.  What the meshes still hold that it does not --
    the target and the material -- moves across before they go.
    """
    empties = [
        obj
        for obj in bpy.data.objects
        if obj.type == "EMPTY" and "dts_decal_index" in obj
    ]
    meshes = [
        obj
        for obj in bpy.data.objects
        if obj.type == "MESH" and "dts_decal_name" in obj
    ]
    if not empties and not meshes:
        return False

    by_index = {}
    for mesh in meshes:
        by_index.setdefault(int(mesh.get("dts_decal_index", -1)), []).append(mesh)

    for empty in empties:
        props = empty.dts_decal
        if props.schema_version >= DECAL_SCHEMA_VERSION:
            continue
        index = int(empty.get("dts_decal_index", 0))
        props.is_dts = True
        props.schema_version = DECAL_SCHEMA_VERSION
        props.decal_name = str(empty.get("dts_decal_name", ""))
        props.index = index
        props.object_name = str(empty.get("dts_decal_object", ""))

        # slot 0 is the highest-detail level, and its target is the one worth
        # pointing at; the others are found by walking the object's LODs
        kin = sorted(by_index.get(index, []), key=lambda m: int(m.get("dts_decal_slot", 0)))
        if kin:
            target = bpy.data.objects.get(str(kin[0].get("dts_decal_target", "")))
            props.target = target if target is not None and target.type == "MESH" else None
            props.subshape = int(kin[0].get("dts_subshape", 0))
            if kin[0].material_slots and kin[0].material_slots[0].material:
                props.material = kin[0].material_slots[0].material
        for key in ("dts_decal_name", "dts_decal_object", "dts_decal_index",
                    "dts_decal_state"):
            if key in empty:
                del empty[key]

    for mesh in meshes:
        data = mesh.data
        bpy.data.objects.remove(mesh)
        if data.users == 0:
            bpy.data.meshes.remove(data)

    if meshes:
        report.append(
            f"{len(meshes)} decal mesh object(s) were removed: a decal is the "
            f"projector empty now, and the faces it covers are recomputed from "
            f"that empty rather than stored. Which faces an imported decal "
            f"covers will not survive unchanged -- check the projectors."
        )
    return True


# the blend bits are read off the node graph; an older version wrote them as
# props beside it, which is two sources for one value
DERIVED_MATERIAL_KEYS = ("dts_translucent", "dts_additive", "dts_subtractive")


def _migrate_reflectance_packing(props) -> None:
    r"""``combine_reflectance`` (bool) -> ``reflectance_packing`` (enum).

    Read off the raw IDProperty rather than through RNA: the bool is not a
    registered property any more, and an unregistered PropertyGroup value is
    still in the .blend under its own name.  Absence means it was never set, so
    there is nothing to convert.

    ``False`` was "write it as its own texture", which the export box can now be
    on while the material still wants -- so it becomes ``SEPARATE``, and the
    exported file is unchanged.  ``True`` becomes ``DEFAULT`` rather than
    ``COMBINE``: it was the old *default*, so it says only that nobody objected,
    and pinning every such material to ``COMBINE`` would leave the new export
    box doing nothing on exactly the scenes most likely to have one.  Combine
    defaults on, so this too exports the same bytes it did before.
    """
    value = props.get(LEGACY_COMBINE_KEY)
    if value is None:
        return
    props.reflectance_packing = "DEFAULT" if bool(value) else "SEPARATE"
    del props[LEGACY_COMBINE_KEY]


def migrate_materials() -> None:
    """Stamp imported materials, and drop the props that became derived.

    Nothing is lost.  The three blend keys were already ignored on export --
    ``_flags_from_blender`` masked them off and recomputed them from the shader
    -- so deleting them changes no exported file, and the reflectance packing
    converts to the value that exports the same bytes.  Reports nothing and does
    not count as a change for that reason.

    Neither conversion is gated on the schema version: the old keys are the
    thing being looked for, so their absence is the only idempotence needed.
    That matters for the packing, which a material with no ``dts_name`` can
    carry too -- it is authorable in a fresh scene, and the ``dts_name`` gate
    below is about imported materials only.
    """
    for mat in bpy.data.materials:
        for key in DERIVED_MATERIAL_KEYS:
            if key in mat:
                del mat[key]
        props = mat.dts_material
        _migrate_reflectance_packing(props)
        if "dts_name" not in mat or props.schema_version >= MATERIAL_SCHEMA_VERSION:
            continue
        props.is_dts = True
        props.schema_version = MATERIAL_SCHEMA_VERSION


def migrate_all() -> list[str]:
    report: list[str] = []
    migrate_materials()
    changed = migrate_meshes(report)
    changed |= migrate_decals(report)
    for obj in bpy.data.objects:
        if obj.type == "ARMATURE":
            changed |= migrate_armature(obj, report)
        elif obj.type == "MESH":
            changed |= migrate_mesh_flags(obj, report)
    for action in bpy.data.actions:
        changed |= migrate_action(action, report)
    if changed:
        note = "; ".join(report)
        for obj in bpy.data.objects:
            if obj.type == "ARMATURE" and obj.dts_shape.is_shape:
                obj.dts_shape.migration_note = note[:900]
    return report


@bpy.app.handlers.persistent
def on_load(_dummy) -> None:
    for line in migrate_all():
        print(f"io_scene_dts: {line}")


def legacy_keys_present() -> list[str]:
    """Legacy keys still in the scene, for the export path to refuse on."""
    found = []
    for obj in bpy.data.objects:
        found += [
            f"{obj.name}.{k}"
            for k in LEGACY_SHAPE_KEYS + LEGACY_PAYLOAD_KEYS + LEGACY_MESH_KEYS
            if k in obj
        ]
    for action in bpy.data.actions:
        found += [f"{action.name}.{k}" for k in LEGACY_ACTION_KEYS if k in action]
    return found

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
    LEGACY_PAYLOAD_KEYS,
    LEGACY_SHAPE_KEYS,
    _loads,
    parse_details,
    parse_ground,
    parse_ifl,
    parse_node_transforms,
    parse_triggers,
)
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


def migrate_armature(obj, report: list) -> bool:
    props = obj.dts_shape
    if props.schema_version >= SCHEMA_VERSION and props.is_shape:
        return False
    if not any(key in obj for key in LEGACY_SHAPE_KEYS):
        return False

    props.is_shape = True
    _fill(props.names, [{"name": n} for n in _loads(obj.get("dts_names_order"), [])])
    _fill(props.details, parse_details(obj.get("dts_details")))
    _fill(props.ifl_materials, parse_ifl(obj.get("dts_ifl_materials")))

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
    if props.schema_version >= SCHEMA_VERSION:
        return False
    if not any(key in action for key in LEGACY_ACTION_KEYS):
        props.schema_version = SCHEMA_VERSION
        return False

    _fill(props.ground, parse_ground(action.get("dts_ground")))
    _fill(props.triggers, parse_triggers(action.get("dts_triggers")))
    _fill(
        props.ifl_matters,
        [{"index": int(i)} for i in _loads(action.get("dts_ifl_matters"), [])],
    )
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


def migrate_all() -> list[str]:
    report: list[str] = []
    changed = migrate_meshes(report)
    for obj in bpy.data.objects:
        if obj.type == "ARMATURE":
            changed |= migrate_armature(obj, report)
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
        found += [f"{obj.name}.{k}" for k in LEGACY_SHAPE_KEYS + LEGACY_PAYLOAD_KEYS if k in obj]
    for action in bpy.data.actions:
        found += [f"{action.name}.{k}" for k in LEGACY_ACTION_KEYS if k in action]
    return found

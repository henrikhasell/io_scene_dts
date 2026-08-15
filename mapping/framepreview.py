"""Previewing vertex animation, and the drivers that do it.

Where the ``frame`` value lives, and why it lives on the armature, is
mapping/objectstate.py -- this module is about making it *visible*: showing the
frame the track asks for, out of the ``frame_NNN`` shape keys the importer built
from the mesh's stored frames.

Frame 0 is the Basis, so frame N is shown by lifting key N to 1 and holding
every other key at 0.  The expression is a hat rather than an equality test so
that a track someone smooths blends between neighbouring frames instead of
snapping -- and so it stays arithmetic, which a driver must be to evaluate in a
file opened without Python auto-run.

Which key is frame N is its *position* in name order, not the number in its
name, because that is the rule the exporter reads them back by
(blender_to_shape.py:_frame_shape_keys).  Preview and file agree by
construction.

Drivers are display only.  Export samples the fcurves and reads the key blocks'
coordinates; neither goes through a key's value.
"""

from __future__ import annotations

import bpy

from .naming import dts_object_and_size
from .objectstate import PREFIXES, ensure_props, path_for, prop_name
from .visibility import refresh_driver_relations

FRAME_PREFIX = PREFIXES["frame"]

__all__ = [
    "FRAME_PREFIX",
    "apply_default_frame",
    "frame_key_blocks",
    "frame_path",
    "frame_prop",
    "wire_frame_drivers",
]


def frame_prop(dts_object_name: str) -> str:
    return prop_name("frame", dts_object_name)


def frame_path(dts_object_name: str) -> str:
    return path_for("frame", dts_object_name)


def frame_key_blocks(me) -> list:
    """The mesh's frame_NNN shape keys, in the order export numbers them."""
    keys = getattr(me, "shape_keys", None)
    if keys is None:
        return []
    return sorted(
        (kb for kb in keys.key_blocks if kb.name.startswith("frame_")),
        key=lambda kb: kb.name,
    )


def _meshes_by_object_name(names) -> dict:
    """{DTS object name: [mesh objects]} for the names asked for.

    Resolved through naming.dts_object_and_size, not the ``dts_object_name``
    property, because a mesh built in Blender has no such property -- it says
    which object it belongs to by its name ("cloth2"), which is the rule the
    exporter groups by.  Reading the property alone previewed imports only.
    """
    found: dict[str, list] = {}
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        base_name, _size = dts_object_and_size(obj)
        if base_name in names:
            found.setdefault(base_name, []).append(obj)
    return found


def apply_default_frame(arm_obj, names) -> None:
    """Start each property at the frame the shape's object state rests on.

    Most rest at 0, but an object whose default state names a later frame is
    modelled in that pose -- resting it at 0 would show a frame the engine
    never does.
    """
    ensure_props(arm_obj, "frame", names)
    for name, meshes in _meshes_by_object_name(names).items():
        arm_obj[frame_prop(name)] = float(meshes[0].get("dts_default_frame", 0.0))


def _add_key_driver(keys, kb, position: int, arm_obj, base_name: str) -> bool:
    path = f'key_blocks["{kb.name}"].value'
    existing = keys.animation_data.drivers if keys.animation_data else []
    for d in existing:
        if d.data_path == path:
            return False  # already wired; re-import must not stack drivers
    drv = keys.driver_add(path).driver
    drv.type = "SCRIPTED"
    var = drv.variables.new()
    var.name = "frame"
    var.type = "SINGLE_PROP"
    var.targets[0].id_type = "OBJECT"
    var.targets[0].id = arm_obj
    var.targets[0].data_path = frame_path(base_name)
    drv.expression = f"max(0.0, 1.0 - abs(frame - {position}))"
    return True


def wire_frame_drivers(arm_obj, names, warnings=None) -> int:
    """Point every frame_NNN key of every mesh built from an object with a
    frame track at the armature's property.  Returns the meshes wired."""
    if not names:
        return 0
    by_name = _meshes_by_object_name(names)
    wired, touched = 0, []
    for base_name in sorted(names):
        meshes = by_name.get(base_name, [])
        if not meshes and warnings is not None:
            warnings.append(f"frame track for object {base_name!r} has no mesh to drive")
        for obj in meshes:
            blocks = frame_key_blocks(obj.data)
            if not blocks:
                # legal: a frame track on a single-frame mesh animates nothing,
                # and the file is what it is -- but silently showing one pose
                # for sixteen keyframes looks like a broken import
                if warnings is not None:
                    warnings.append(
                        f"frame track for object {base_name!r}: {obj.name} has no "
                        f"frame_NNN shape keys, so nothing previews"
                    )
                continue
            for position, kb in enumerate(blocks, start=1):
                _add_key_driver(obj.data.shape_keys, kb, position, arm_obj, base_name)
            wired += 1
            touched.append(obj.data.shape_keys)
    refresh_driver_relations(touched)
    return wired

"""Object visibility tracks, previewable in Blender.

A sequence can animate an object's ``vis`` (0..1) alongside its bone channels.
Those channels belong to mesh objects, not the armature — and one DTS object
maps to one Blender mesh *per detail level*, so light_male's ``Jetfire`` is ten
objects.  Keyframing them directly would mean a slot and an NLA track per LOD
copy per sequence, all of which would have to be muted and scaled in lockstep
with the armature's.

Instead the value is keyframed as a custom property on the armature, in the
same slot as the bones, and each mesh reads it through a driver.  One ID stays
animated, the NLA model is untouched, and retiming a strip retimes visibility
for free because a driver reads whatever the strip currently produces.

Drivers are display only.  Export samples the fcurves (or the stored
``dts_object_anim``) and never looks at them.
"""

import json

import bpy

VIS_PREFIX = "dts_vis_"


def vis_prop(dts_object_name: str) -> str:
    return f"{VIS_PREFIX}{dts_object_name}"


def vis_path(dts_object_name: str) -> str:
    # bracketed string key, so DTS names with spaces or punctuation are fine
    return f'["{vis_prop(dts_object_name)}"]'


def tracks_of(action: bpy.types.Action) -> dict:
    return json.loads(action.get("dts_object_anim", "{}") or "{}")


def animated_object_names(actions) -> set:
    """DTS object names carrying a vis track across the given actions."""
    names = set()
    for action in actions:
        for base_name, tracks in tracks_of(action).items():
            if tracks.get("vis"):
                names.add(base_name)
    return names


def ensure_props(arm_obj, names, default: float = 1.0) -> None:
    """The ID property must exist before an fcurve or driver can resolve it."""
    for name in names:
        if vis_prop(name) not in arm_obj.keys():
            arm_obj[vis_prop(name)] = float(default)


def write_vis_fcurves(bag, action: bpy.types.Action, arm_obj) -> set:
    """Keyframe each animated object's vis track into the action's armature
    slot.  Returns the DTS object names that got a track."""
    written = set()
    for base_name, tracks in tracks_of(action).items():
        vis = tracks.get("vis")
        if not vis:
            continue
        ensure_props(arm_obj, [base_name])
        fc = bag.fcurves.new(data_path=vis_path(base_name), index=0)
        fc.keyframe_points.add(len(vis))
        for i, v in enumerate(vis):
            kp = fc.keyframe_points[i]
            kp.co = (i + 1, float(v))
            kp.interpolation = "LINEAR"
        fc.update()
        written.add(base_name)
    return written


def _add_driver(obj, path, index, arm_obj, base_name, expression):
    existing = obj.animation_data.drivers if obj.animation_data else []
    for d in existing:
        if d.data_path == path and (index is None or d.array_index == index):
            return None  # already wired; re-import must not stack drivers
    fcurve = obj.driver_add(path) if index is None else obj.driver_add(path, index)
    drv = fcurve.driver
    drv.type = "SCRIPTED"
    var = drv.variables.new()
    var.name = "vis"
    var.type = "SINGLE_PROP"
    var.targets[0].id = arm_obj
    var.targets[0].data_path = vis_path(base_name)
    drv.expression = expression
    return fcurve


def wire_drivers(arm_obj, names, warnings=None) -> int:
    """Point every mesh built from an animated DTS object at the armature's
    property.  Returns the number of Blender objects wired."""
    if not names:
        return 0
    by_name = {}
    for obj in bpy.data.objects:
        if obj.type == "MESH" and obj.get("dts_object_name") in names:
            by_name.setdefault(obj["dts_object_name"], []).append(obj)

    wired = 0
    for base_name in sorted(names):
        meshes = by_name.get(base_name, [])
        if not meshes and warnings is not None:
            warnings.append(
                f"visibility track for object {base_name!r} has no mesh to drive"
            )
        for obj in meshes:
            # vis is a 0..1 scalar, not a toggle: alpha carries the fade,
            # and the hide flags only kick in once it reaches zero
            _add_driver(obj, "color", 3, arm_obj, base_name, "vis")
            _add_driver(obj, "hide_viewport", None, arm_obj, base_name, "vis <= 0.0")
            _add_driver(obj, "hide_render", None, arm_obj, base_name, "vis <= 0.0")
            wired += 1
    return wired

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

from .materials import emission_of_add_shader, fade_emission, remember_blend_state

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


def fractional_object_names(actions) -> set:
    """Names whose vis track holds values between 0 and 1.

    A binary track is a swap, and the hide drivers cover it without making the
    material transparent.  Only a genuine fade needs alpha — the generator's
    power lights pulse 0.1..1.0, while its panel swaps are 1/0.
    """
    names = set()
    for action in actions:
        for base_name, tracks in tracks_of(action).items():
            vis = tracks.get("vis") or []
            if any(0.0 < float(v) < 1.0 for v in vis):
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


_PENDING_REFRESH = set()
_REFRESH_PASSES = [0]
# The first pass has to land after the import's own depsgraph evaluation has
# settled; firing immediately (interval 0) runs too early and does nothing.
_REFRESH_DELAY = 0.1


def _do_refresh():
    """Re-assign every pending driver's expression, marking it dirty so the
    dependency graph rebuilds its relations."""
    for name in list(_PENDING_REFRESH):
        obj = bpy.data.objects.get(name)
        if obj is not None and obj.animation_data is not None:
            for fcurve in obj.animation_data.drivers:
                fcurve.driver.expression = fcurve.driver.expression
    _REFRESH_PASSES[0] -= 1
    if _REFRESH_PASSES[0] > 0:
        return _REFRESH_DELAY
    _PENDING_REFRESH.clear()
    return None


def refresh_driver_relations(objects) -> None:
    """Queue a dependency-relation rebuild for these objects' drivers.

    A driver added while its target property is *already* animated gets no
    relation to that animation, so the strip moves the property and nothing
    follows.  Re-assigning an expression marks the driver dirty — but only
    once the importing operator has returned, so it is deferred to a timer.
    There is no public "rebuild relations" call and drivers.update() does not
    do it.

    Sequences that animate a bone as well as a property are unaffected: the
    bone channel gives the armature a real animation component for the driver
    to depend on.  A decal-only sequence like light_male's Damage has none.
    """
    _PENDING_REFRESH.update(
        obj.name for obj in objects if obj.animation_data is not None
    )
    if _PENDING_REFRESH and not bpy.app.timers.is_registered(_do_refresh):
        _REFRESH_PASSES[0] = 2
        bpy.app.timers.register(_do_refresh, first_interval=_REFRESH_DELAY)


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


def apply_default_vis(arm_obj, names) -> None:
    """Start each property at the shape's default object state.

    Defaulting to 1.0 shows everything at once, which is wrong for shapes that
    use visibility to pick a state: the large generator's "ON" meshes and its
    destroyed hulk are all hidden at rest, and drawing the hulk buries the
    machine it replaces.
    """
    defaults = {}
    for obj in bpy.data.objects:
        name = obj.get("dts_object_name")
        if obj.type == "MESH" and name in names and name not in defaults:
            defaults[name] = float(obj.get("dts_default_vis", 1.0))
    for name, value in defaults.items():
        arm_obj[vis_prop(name)] = value


def _wire_material_alpha(mat) -> bool:
    """Feed the object's alpha into the shader, so a per-object fade shows.

    Object alpha defaults to 1.0, so this is a no-op for every other mesh
    sharing the material — only the driven ones change.
    """
    nt = getattr(mat, "node_tree", None)
    if nt is None:
        return False
    if any(n.type == "OBJECT_INFO" for n in nt.nodes):
        return False  # already wired; re-import must not stack nodes
    # export reads MAT_TRANSLUCENT off the material, and the fade below forces
    # blending whatever the shape said
    remember_blend_state(mat)
    emission = emission_of_add_shader(nt)
    if emission is not None:
        # additive/subtractive: no Principled to fade, so scale the emission
        return fade_emission(nt, emission)
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return False

    info = nt.nodes.new("ShaderNodeObjectInfo")
    info.location = (bsdf.location.x - 600, bsdf.location.y - 300)
    if "Alpha" not in info.outputs:
        nt.nodes.remove(info)
        return False

    alpha_in = bsdf.inputs["Alpha"]
    if alpha_in.is_linked:
        # keep the texture's own alpha (MAT_TRANSLUCENT) and modulate it
        source = alpha_in.links[0].from_socket
        mul = nt.nodes.new("ShaderNodeMath")
        mul.operation = "MULTIPLY"
        mul.location = (bsdf.location.x - 300, bsdf.location.y - 300)
        nt.links.new(source, mul.inputs[0])
        nt.links.new(info.outputs["Alpha"], mul.inputs[1])
        nt.links.new(mul.outputs[0], alpha_in)
    else:
        nt.links.new(info.outputs["Alpha"], alpha_in)

    # Blender 4.2+ replaced blend_method with surface_render_method
    if hasattr(mat, "blend_method"):
        mat.blend_method = "BLEND"
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    return True


def wire_fade_materials(fractional_names) -> int:
    """Wire object alpha into the materials of meshes that genuinely fade."""
    done, count = set(), 0
    for obj in bpy.data.objects:
        if obj.type != "MESH" or obj.get("dts_object_name") not in fractional_names:
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None or mat.name in done:
                continue
            done.add(mat.name)
            if _wire_material_alpha(mat):
                count += 1
    return count


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
    refresh_driver_relations(m for meshes in by_name.values() for m in meshes)
    return wired

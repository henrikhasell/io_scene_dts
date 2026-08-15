"""Previewing object visibility, and the drivers that do it.

Where the ``vis`` value lives, and why it lives on the armature, is
mapping/objectstate.py -- this module is about making it *visible*: hiding a
mesh whose object is switched off, and fading one whose object is part-way.

Drivers are display only.  Export samples the fcurves.
"""

import bpy

from .materials import emission_of_add_shader, fade_emission, remember_blend_state
from .objectstate import (
    PREFIXES,
    animated_object_names,
    fractional_object_names,
    path_for,
    prop_name,
)

VIS_PREFIX = PREFIXES["vis"]

__all__ = [
    "VIS_PREFIX",
    "animated_object_names",
    "apply_default_vis",
    "flush_driver_relations",
    "fractional_object_names",
    "refresh_driver_relations",
    "vis_path",
    "vis_prop",
    "wire_drivers",
    "wire_fade_materials",
]


def vis_prop(dts_object_name: str) -> str:
    return prop_name("vis", dts_object_name)


def vis_path(dts_object_name: str) -> str:
    return path_for("vis", dts_object_name)


def ensure_props(arm_obj, names, default: float = 1.0) -> None:
    """The ID property must exist before an fcurve or driver can resolve it."""
    for name in names:
        if vis_prop(name) not in arm_obj.keys():
            arm_obj[vis_prop(name)] = float(default)


_PENDING_REFRESH = set()
_REFRESH_PASSES = [0]
# The first pass has to land after the import's own depsgraph evaluation has
# settled; firing immediately (interval 0) runs too early and does nothing.
_REFRESH_DELAY = 0.1


# Drivers do not only live on objects: the frame preview's live on the mesh's
# shape-key datablock (mapping/framepreview.py), which is a different bpy.data
# collection and cannot be looked up by name alone.
_COLLECTION_OF = {"Object": "objects", "Key": "shape_keys"}


def _dirty_pending() -> int:
    """Re-assign every pending driver's expression, marking it dirty so the
    dependency graph rebuilds its relations.  Returns the datablocks touched."""
    touched = 0
    for collection, name in list(_PENDING_REFRESH):
        owner = getattr(bpy.data, collection).get(name)
        if owner is not None and owner.animation_data is not None:
            for fcurve in owner.animation_data.drivers:
                fcurve.driver.expression = fcurve.driver.expression
            touched += 1
    return touched


def flush_driver_relations() -> int:
    """Dirty every queued driver now and stop waiting for an evaluation.

    Idempotent, and what a caller that has just finished building a scene by
    script should call instead of hoping an update lands.
    """
    touched = _dirty_pending()
    _PENDING_REFRESH.clear()
    _FLUSHES_LEFT[0] = 0
    _unregister_handler()
    return touched


_FLUSHES_LEFT = [0]
_FLUSHING = [False]
# The first evaluation after wiring is still inside the importing operator,
# where dirtying does nothing; the one after it is not.  Flushing on a few
# consecutive updates covers both without having to detect which is which.
_FLUSH_UPDATES = 3


@bpy.app.handlers.persistent
def _on_depsgraph_update(scene, depsgraph=None):
    """Flush on the evaluations that follow wiring.

    The timer below only runs in an interactive Blender; a background session
    -- the integration tests, and anything scripted -- never fires it, and the
    drivers sit there valid and unevaluated.  This fires in both.
    """
    if _FLUSHING[0]:
        return  # dirtying triggers the update this handler listens for
    _FLUSHING[0] = True
    try:
        if _dirty_pending():
            # dirtying alone leaves the geometry a step behind: the shape-key
            # drivers evaluate, and the mesh that reads them was already
            # deformed with the previous values.  Re-evaluating here settles it
            # within the frame the user is looking at.
            bpy.context.view_layer.update()
        _FLUSHES_LEFT[0] -= 1
        if _FLUSHES_LEFT[0] <= 0:
            _PENDING_REFRESH.clear()
            _unregister_handler()
    finally:
        _FLUSHING[0] = False


def _unregister_handler() -> None:
    for handlers in (
        bpy.app.handlers.depsgraph_update_post,
        bpy.app.handlers.frame_change_post,
    ):
        if _on_depsgraph_update in handlers:
            handlers.remove(_on_depsgraph_update)


def _do_refresh():
    _dirty_pending()
    _REFRESH_PASSES[0] -= 1
    if _REFRESH_PASSES[0] > 0:
        return _REFRESH_DELAY
    _PENDING_REFRESH.clear()
    return None


def refresh_driver_relations(owners) -> None:
    """Queue a dependency-relation rebuild for these datablocks' drivers.

    A driver added while its target property is *already* animated gets no
    relation to that animation, so the strip moves the property and nothing
    follows.  Re-assigning an expression marks the driver dirty — but only
    once the importing operator has returned, so it is deferred: to the next
    depsgraph evaluation, and to a timer behind it.  There is no public
    "rebuild relations" call and drivers.update() does not do it.

    Sequences that animate a bone as well as a property are unaffected: the
    bone channel gives the armature a real animation component for the driver
    to depend on.  A decal-only sequence like light_male's Damage has none.
    """
    _PENDING_REFRESH.update(
        (_COLLECTION_OF[owner.rna_type.identifier], owner.name)
        for owner in owners
        if owner is not None
        and owner.animation_data is not None
        and owner.rna_type.identifier in _COLLECTION_OF
    )
    if not _PENDING_REFRESH:
        return
    _FLUSHES_LEFT[0] = _FLUSH_UPDATES
    # depsgraph_update_post covers editing, frame_change_post covers scrubbing
    # -- and a background session only ever fires the second
    for handlers in (
        bpy.app.handlers.depsgraph_update_post,
        bpy.app.handlers.frame_change_post,
    ):
        if _on_depsgraph_update not in handlers:
            handlers.append(_on_depsgraph_update)
    if not bpy.app.timers.is_registered(_do_refresh):
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

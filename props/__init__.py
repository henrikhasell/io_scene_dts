"""First-class Blender properties for the DTS data that had no home.

Most of what the add-on records rides on plain ID custom properties, which are
editable in the N-panel's Custom Properties and good enough for a number or a
checkbox.  A handful of things were not numbers, though: name tables, detail
tables, IFL entries, material orderings, ground frames and triggers were
serialized to JSON strings.  That form is technically present in the UI and not
usable from it.

This package gives them typed collections instead, so a UIList can show them and
an operator can add and remove entries.  Groups carry a ``schema_version`` so a
later version has something to key a conversion on; nothing reads it today.

Nothing is imported at module scope on purpose: importing this package must not
drag Blender in behind it, or the fast test loop cannot reach it.
"""

from __future__ import annotations


def _classes():
    """Leaf groups first: an item type must be registered before the group
    holding a CollectionProperty of it."""
    from . import decal, material, mesh, node, scene, sequence, shape

    return (
        shape.DtsNameItem,
        shape.DtsDetailItem,
        shape.DtsMaterialRef,
        shape.DtsShapeProps,
        mesh.DtsMeshProps,
        material.DtsIflFrame,
        material.DtsMaterialProps,
        decal.DtsDecalProps,
        node.DtsNodeProps,
        scene.DtsSceneProps,
        sequence.DtsGroundItem,
        sequence.DtsTriggerItem,
        sequence.DtsIflMatterItem,
        sequence.DtsSequenceProps,
    )


def register() -> None:
    import bpy

    from . import decal, material, mesh, node, scene, sequence, shape

    for cls in _classes():
        bpy.utils.register_class(cls)
    # pointers last: the groups they name have to exist first
    bpy.types.Scene.dts_scene = bpy.props.PointerProperty(type=scene.DtsSceneProps)
    bpy.types.Object.dts_shape = bpy.props.PointerProperty(type=shape.DtsShapeProps)
    bpy.types.Object.dts_mesh = bpy.props.PointerProperty(type=mesh.DtsMeshProps)
    bpy.types.Object.dts_decal = bpy.props.PointerProperty(type=decal.DtsDecalProps)
    bpy.types.Material.dts_material = bpy.props.PointerProperty(
        type=material.DtsMaterialProps
    )
    bpy.types.Bone.dts_node = bpy.props.PointerProperty(type=node.DtsNodeProps)
    bpy.types.Action.dts_sequence_props = bpy.props.PointerProperty(
        type=sequence.DtsSequenceProps
    )


def unregister() -> None:
    import bpy

    for owner, name in (
        (bpy.types.Action, "dts_sequence_props"),
        (bpy.types.Bone, "dts_node"),
        (bpy.types.Material, "dts_material"),
        (bpy.types.Object, "dts_decal"),
        (bpy.types.Object, "dts_mesh"),
        (bpy.types.Object, "dts_shape"),
        (bpy.types.Scene, "dts_scene"),
    ):
        if hasattr(owner, name):
            delattr(owner, name)
    for cls in reversed(_classes()):
        bpy.utils.unregister_class(cls)

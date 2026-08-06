"""First-class Blender properties for the DTS data that had no home.

Most of what the add-on records rides on plain ID custom properties, which are
editable in the N-panel's Custom Properties and good enough for a number or a
checkbox.  A handful of things were not numbers, though: name tables, detail
tables, IFL entries, material orderings, ground frames and triggers were
serialized to JSON strings.  That form is technically present in the UI and not
usable from it.

This package gives them typed collections instead, so a UIList can show them and
an operator can add and remove entries.  Everything is versioned by
``schema_version`` and converted from the old form on load, so an existing
.blend keeps working; see ``props/migrate.py``.

Nothing is imported at module scope on purpose: ``props/legacy.py`` is pure
parsing with no ``bpy`` in it, and importing this package must not drag Blender
in behind it, or the fast test loop cannot reach it.
"""

from __future__ import annotations


def _classes():
    """Leaf groups first: an item type must be registered before the group
    holding a CollectionProperty of it."""
    from . import decal, mesh, node, sequence, shape

    return (
        shape.DtsNameItem,
        shape.DtsDetailItem,
        shape.DtsMaterialRef,
        shape.DtsIflItem,
        shape.DtsShapeProps,
        mesh.DtsMeshProps,
        decal.DtsDecalProps,
        node.DtsNodeProps,
        sequence.DtsGroundItem,
        sequence.DtsTriggerItem,
        sequence.DtsIflMatterItem,
        sequence.DtsSequenceProps,
    )


def register() -> None:
    import bpy

    from . import decal, mesh, migrate, node, sequence, shape

    for cls in _classes():
        bpy.utils.register_class(cls)
    # pointers last: the groups they name have to exist first
    bpy.types.Object.dts_shape = bpy.props.PointerProperty(type=shape.DtsShapeProps)
    bpy.types.Object.dts_mesh = bpy.props.PointerProperty(type=mesh.DtsMeshProps)
    bpy.types.Object.dts_decal = bpy.props.PointerProperty(type=decal.DtsDecalProps)
    bpy.types.Bone.dts_node = bpy.props.PointerProperty(type=node.DtsNodeProps)
    bpy.types.Action.dts_sequence_props = bpy.props.PointerProperty(
        type=sequence.DtsSequenceProps
    )
    if migrate.on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(migrate.on_load)


def unregister() -> None:
    import bpy

    from . import migrate

    if migrate.on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(migrate.on_load)
    for owner, name in (
        (bpy.types.Action, "dts_sequence_props"),
        (bpy.types.Bone, "dts_node"),
        (bpy.types.Object, "dts_decal"),
        (bpy.types.Object, "dts_mesh"),
        (bpy.types.Object, "dts_shape"),
    ):
        if hasattr(owner, name):
            delattr(owner, name)
    for cls in reversed(_classes()):
        bpy.utils.unregister_class(cls)

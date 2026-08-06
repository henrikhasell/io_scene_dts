"""Add, remove and reorder entries in the DTS tables, plus scene migration.

A CollectionProperty has no built-in add/remove buttons; every UIList needs
operators beside it.  One generic pair covers all of them by naming the
collection through a data path rather than hard-coding one per table.
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator


def _resolve(context, path: str):
    """(collection, active-index property owner, index attribute name).

    ``path`` is like "object.dts_shape.details": everything up to the last
    component names the group, the last names the collection, and the active
    index is that name plus "_index" by convention.

    "action." is a special root, because an Action is not reachable from
    ``context`` by attribute at all -- which is the same reason its properties
    need panels in the animation editors rather than a Properties tab.
    """
    owner_path, _, name = path.rpartition(".")
    if owner_path == "action":
        from .panels import _action_in_context

        action = _action_in_context(context)
        if action is None:
            return None, None, None
        owner = action.dts_sequence_props
    else:
        owner = context
        for part in owner_path.split("."):
            owner = getattr(owner, part, None)
            if owner is None:
                return None, None, None
    collection = getattr(owner, name, None)
    if collection is None:
        return None, None, None
    return collection, owner, f"{name}_index"


class DTS_OT_list_add(Operator):
    bl_idname = "io_scene_dts.list_add"
    bl_label = "Add Entry"
    bl_description = "Add an entry to this DTS table"
    bl_options = {"REGISTER", "UNDO"}

    path: StringProperty()

    def execute(self, context):
        collection, owner, index_attr = _resolve(context, self.path)
        if collection is None:
            self.report({"ERROR"}, f"no collection at {self.path!r}")
            return {"CANCELLED"}
        collection.add()
        setattr(owner, index_attr, len(collection) - 1)
        return {"FINISHED"}


class DTS_OT_list_remove(Operator):
    bl_idname = "io_scene_dts.list_remove"
    bl_label = "Remove Entry"
    bl_description = "Remove the selected entry from this DTS table"
    bl_options = {"REGISTER", "UNDO"}

    path: StringProperty()

    def execute(self, context):
        collection, owner, index_attr = _resolve(context, self.path)
        if collection is None:
            return {"CANCELLED"}
        index = getattr(owner, index_attr)
        if not 0 <= index < len(collection):
            return {"CANCELLED"}
        collection.remove(index)
        setattr(owner, index_attr, min(index, len(collection) - 1))
        return {"FINISHED"}


class DTS_OT_list_move(Operator):
    bl_idname = "io_scene_dts.list_move"
    bl_label = "Move Entry"
    bl_description = "Move the selected entry.  Order is load-bearing in these tables"
    bl_options = {"REGISTER", "UNDO"}

    path: StringProperty()
    direction: EnumProperty(items=[("UP", "Up", ""), ("DOWN", "Down", "")])

    def execute(self, context):
        collection, owner, index_attr = _resolve(context, self.path)
        if collection is None:
            return {"CANCELLED"}
        index = getattr(owner, index_attr)
        target = index - 1 if self.direction == "UP" else index + 1
        if not (0 <= index < len(collection) and 0 <= target < len(collection)):
            return {"CANCELLED"}
        collection.move(index, target)
        setattr(owner, index_attr, target)
        return {"FINISHED"}


def _armature_of(obj):
    """The shape's armature: a mesh is parented to it, or skinned to it."""
    parent = obj.parent
    while parent is not None and parent.type != "ARMATURE":
        parent = parent.parent
    if parent is not None:
        return parent
    return next(
        (m.object for m in obj.modifiers if m.type == "ARMATURE" and m.object), None
    )


class DTS_OT_add_decal(Operator):
    """Make a decal from the faces selected on a DTS mesh.

    The selection is what the projector gets *fitted to*; the decal itself is
    the empty this leaves behind, and moving that empty afterwards is what
    moves the decal.  Decals are the add-on's showcase mapping -- a projection
    in Blender, indices and planes in the file -- and until this existed they
    could be imported and edited but not made.
    """

    bl_idname = "io_scene_dts.add_decal"
    bl_label = "Add DTS Decal"
    bl_description = (
        "Fit a decal projector to the selected faces.  The decal is the empty "
        "this creates: move or scale it to move the decal"
    )
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(
        name="Name",
        description="The decal's name.  Not unique in real shapes; the index is its identity",
        default="decal",
    )
    all_details: BoolProperty(
        name="All Detail Levels",
        description=(
            "Project onto every detail level of the same object, so the decal does "
            "not vanish as the engine drops LOD.  Every shipped decal does this"
        ),
        default=True,
    )

    @classmethod
    def poll(cls, context):
        obj = context.object
        # deliberately not "dts_object_name in obj": that is written by the
        # importer, and requiring it would make this work only on shapes that
        # came from a file -- which is the thing decals could not do before.
        return (
            obj is not None
            and obj.type == "MESH"
            and _armature_of(obj) is not None
        )

    def execute(self, context):
        from ..mapping.decals import create_decal

        target = context.object
        # face selection only exists on the mesh once edit mode has flushed it
        if target.mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")

        arm = _armature_of(target)
        if arm is None:
            self.report({"ERROR"}, "the decal's target must hang off the shape's armature")
            return {"CANCELLED"}

        material = target.active_material
        try:
            index, projector = create_decal(
                arm, target, name=self.name, material=material,
                all_details=self.all_details,
            )
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        self.report({"INFO"}, f"decal {self.name!r} (#{index}): {projector.name}")
        return {"FINISHED"}


class DTS_OT_refresh_decal(Operator):
    """Recompute which faces a decal covers, from where its empty now is.

    Coverage is derived, so the preview mask has to be told to catch up.
    Export never reads the cache -- it calls covered_faces() itself -- so this
    only ever changes what you see, never what you get.
    """

    bl_idname = "io_scene_dts.refresh_decal"
    bl_label = "Refresh DTS Decal Coverage"
    bl_description = (
        "Recompute the faces this decal covers from its current position, size "
        "and depth.  Affects the viewport preview only; export recomputes anyway"
    )
    bl_options = {"REGISTER", "UNDO"}

    all_decals: BoolProperty(
        name="Every Decal",
        description="Refresh every decal in the scene, not just the selected one",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == "EMPTY" and obj.dts_decal.is_dts

    def execute(self, context):
        from ..mapping.decals import decal_objects, refresh_coverage

        targets = decal_objects() if self.all_decals else [context.object]
        faces = sum(refresh_coverage(d) for d in targets)
        self.report({"INFO"}, f"{len(targets)} decal(s), {faces} face(s) covered")
        return {"FINISHED"}


class DTS_OT_migrate_scene(Operator):
    bl_idname = "io_scene_dts.migrate_scene"
    bl_label = "Convert DTS Data From an Older Version"
    bl_description = (
        "Convert the JSON blobs an earlier version of this add-on wrote into the "
        "editable tables, and discard any pickled mesh payloads.  Runs automatically "
        "when a file is opened with the add-on already enabled"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from ..props import migrate

        report = migrate.migrate_all()
        if not report:
            self.report({"INFO"}, "nothing to convert")
        else:
            for line in report:
                self.report({"WARNING"}, line)
        return {"FINISHED"}


class DTS_OT_dismiss_migration_note(Operator):
    bl_idname = "io_scene_dts.dismiss_migration_note"
    bl_label = "Dismiss"
    bl_description = "Hide the note about what conversion dropped"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.object
        if obj is not None:
            obj.dts_shape.migration_note = ""
        return {"FINISHED"}


def list_buttons(layout, path: str, *, move: bool = False) -> None:
    """The add/remove (and optionally reorder) column beside a UIList."""
    column = layout.column(align=True)
    column.operator(DTS_OT_list_add.bl_idname, icon="ADD", text="").path = path
    column.operator(DTS_OT_list_remove.bl_idname, icon="REMOVE", text="").path = path
    if move:
        column.separator()
        up = column.operator(DTS_OT_list_move.bl_idname, icon="TRIA_UP", text="")
        up.path, up.direction = path, "UP"
        down = column.operator(DTS_OT_list_move.bl_idname, icon="TRIA_DOWN", text="")
        down.path, down.direction = path, "DOWN"


CLASSES = (
    DTS_OT_add_decal,
    DTS_OT_refresh_decal,
    DTS_OT_list_add,
    DTS_OT_list_remove,
    DTS_OT_list_move,
    DTS_OT_migrate_scene,
    DTS_OT_dismiss_migration_note,
)

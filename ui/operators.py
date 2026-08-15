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


class DTS_OT_refresh_ifl(Operator):
    """Rebuild the flipbook preview from the frame list"""

    bl_idname = "io_scene_dts.refresh_ifl"
    bl_label = "Refresh IFL Preview"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        mat = getattr(context, "material", None)
        return mat is not None and mat.dts_material.is_ifl

    def execute(self, context):
        from ..mapping.materials import build_ifl_preview

        frames = build_ifl_preview(context.material)
        self.report({"INFO"}, f"IFL preview rebuilt over {frames} frame(s)")
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


class DTS_OT_add_reflectance(Operator):
    """Give a material a reflectance map, from nothing.

    The map is a node, not a property -- the shader is where it lives, so that
    export and the viewport read one thing -- which leaves a material that has
    never had one with nothing for a panel to draw.  This is the button that
    makes the node, and the image slot appears once it exists.  Without it the
    feature is authorable only by hand in the node editor, which is the
    billboard problem in CLAUDE.md wearing a different hat.
    """

    bl_idname = "io_scene_dts.add_reflectance"
    bl_label = "Add Reflectance Map"
    bl_description = (
        "Add an environment-map mask to this material and wire it up.  The mask "
        "says which texels reflect; pick or paint an image for it afterwards"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        mat = getattr(context, "material", None)
        return mat is not None and mat.use_nodes

    def execute(self, context):
        from ..mapping import envmap

        mat = context.material
        if envmap.group_node(mat) is not None:
            self.report({"INFO"}, "this material already has a reflectance map")
            return {"CANCELLED"}

        nt = mat.node_tree
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            self.report(
                {"ERROR"},
                "this material has no Principled BSDF to mix the reflection over; "
                "additive and subtractive materials never env-map in the engine",
            )
            return {"CANCELLED"}

        node = nt.nodes.new("ShaderNodeTexImage")
        node.location = (bsdf.location.x - 400, bsdf.location.y - 480)
        # a mask is data, not colour: an sRGB curve would change which texels
        # the engine reads as reflective
        node.label = "Reflectance"
        if not envmap.wire(mat, node.outputs["Color"]):
            nt.nodes.remove(node)
            return {"CANCELLED"}
        # `dts_never_env_map` is deliberately not touched.  Export already
        # clears the bit for any material showing a reflectance map and sets it
        # for any fresh material without one, so writing it here would be a
        # stored copy of something derived -- and it would outlive the map,
        # leaving a material env-mapping with nothing to mask it.
        return {"FINISHED"}


class DTS_OT_remove_reflectance(Operator):
    bl_idname = "io_scene_dts.remove_reflectance"
    bl_label = "Remove Reflectance Map"
    bl_description = "Take the environment-map mask and its preview back out of this material"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from ..mapping import envmap

        mat = getattr(context, "material", None)
        return mat is not None and envmap.group_node(mat) is not None

    def execute(self, context):
        from ..mapping import envmap

        mat = context.material
        socket = envmap.mask_socket(mat)
        source = socket.node if socket is not None else None
        envmap.unwire(mat)
        # only if it was there for the mask alone.  A combined material feeds
        # the mask off the diffuse node's alpha, and that node is still the
        # material's texture.
        if source is not None and source.type == "TEX_IMAGE":
            if not any(out.is_linked for out in source.outputs):
                mat.node_tree.nodes.remove(source)
        return {"FINISHED"}


class DTS_OT_rebuild_env_map(Operator):
    """Move a reflectance map from Metallic onto the environment-map preview.

    Materials imported before ``mapping/envmap.py`` existed have theirs on the
    Principled's Metallic input.  Those still export correctly, so this is not a
    migration and does not run on load: it changes the user's node tree, which
    is theirs, and it is offered rather than done.
    """

    bl_idname = "io_scene_dts.rebuild_env_map"
    bl_label = "Rebuild Environment Preview"
    bl_description = (
        "Re-wire reflectance maps that are still on the Principled BSDF's Metallic "
        "input so they show the engine's environment map instead"
    )
    bl_options = {"REGISTER", "UNDO"}

    all_materials: BoolProperty(
        name="All Materials",
        description="Every material in the file, not just the active one",
        default=False,
    )

    def execute(self, context):
        from ..mapping import envmap, materials

        if self.all_materials:
            targets = list(bpy.data.materials)
        else:
            targets = [context.material] if getattr(context, "material", None) else []

        moved = 0
        for mat in targets:
            if not mat.use_nodes or envmap.group_node(mat) is not None:
                continue
            node = materials._image_node_feeding(mat, "Metallic")
            if node is None:
                continue
            bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
            socket = bsdf.inputs["Metallic"]
            source = socket.links[0].from_socket
            for link in list(socket.links):
                mat.node_tree.links.remove(link)
            socket.default_value = 0.0
            if envmap.wire(mat, source):
                moved += 1
        self.report({"INFO"}, f"re-wired {moved} material(s)")
        return {"FINISHED"}


class DTS_OT_rebuild_decal_preview(Operator):
    """Build a decal's shader branch again from what it says now.

    The sibling of ``rebuild_env_map``, and it exists for the same reason: a
    branch is built once and then left alone, so an improvement to how decals
    preview reaches new imports and no existing scene.  A scene saved while
    decals previewed as unlit Emission keeps that until something rebuilds it.

    Preview only.  Export recomputes coverage and reads the decal's properties,
    never this graph, so nothing here changes what gets written.
    """

    bl_idname = "io_scene_dts.rebuild_decal_preview"
    bl_label = "Rebuild Decal Preview"
    bl_description = (
        "Rebuild the shader branch that previews this decal, picking up its "
        "current material and how the add-on now shades decals.  Affects the "
        "viewport only; export is unchanged"
    )
    bl_options = {"REGISTER", "UNDO"}

    all_decals: BoolProperty(
        name="Every Decal",
        description="Rebuild every decal in the scene, not just the selected one",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        from ..mapping.decals import decal_objects

        obj = context.object
        if obj is not None and obj.type == "EMPTY" and obj.dts_decal.is_dts:
            return True
        return bool(decal_objects())

    def execute(self, context):
        from ..mapping.decals import decal_objects, rebuild_branch

        obj = context.object
        if self.all_decals or obj is None or not obj.dts_decal.is_dts:
            targets = decal_objects()
        else:
            targets = [obj]
        # index order, so chained branches come back in the order they were in
        rebuilt = sum(1 for d in sorted(targets, key=lambda o: o.dts_decal.index)
                      if rebuild_branch(d))
        self.report({"INFO"}, f"rebuilt {rebuilt} decal preview(s)")
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
    DTS_OT_add_reflectance,
    DTS_OT_remove_reflectance,
    DTS_OT_rebuild_env_map,
    DTS_OT_rebuild_decal_preview,
    DTS_OT_list_add,
    DTS_OT_list_remove,
    DTS_OT_list_move,
    DTS_OT_migrate_scene,
    DTS_OT_refresh_ifl,
    DTS_OT_dismiss_migration_note,
)

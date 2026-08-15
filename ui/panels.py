"""Where the DTS tables are edited.

The shape tables hang off the armature, so they go in Object Properties; a
node's stored rest transform goes in Bone Properties beside the bone it
describes.

Sequences are the awkward one.  Blender's Properties editor has no tab for an
Action at all, so a sequence's ground frames and triggers cannot live there --
they get panels in the Dope Sheet and NLA sidebars instead, sharing one body.
"""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..dtslib.types import MAT_ADDITIVE, MAT_SUBTRACTIVE, MAT_TRANSLUCENT
from ..mapping import materials
from .operators import (
    DTS_OT_add_decal,
    DTS_OT_add_reflectance,
    DTS_OT_rebuild_env_map,
    DTS_OT_refresh_ifl,
    DTS_OT_remove_reflectance,
    DTS_OT_dismiss_migration_note,
    DTS_OT_migrate_scene,
    list_buttons,
)


def _is_shape(obj) -> bool:
    return obj is not None and obj.type == "ARMATURE" and obj.dts_shape.is_shape


def _table(layout, owner, collection_name: str, list_id: str, path: str, *, move=False):
    row = layout.row()
    row.template_list(
        list_id, "", owner, collection_name, owner, f"{collection_name}_index", rows=4
    )
    list_buttons(row, path, move=move)


class OBJECT_PT_dts_shape(Panel):
    bl_label = "DTS Shape"
    bl_idname = "OBJECT_PT_dts_shape"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    @classmethod
    def poll(cls, context):
        return _is_shape(context.object)

    def draw(self, context):
        layout = self.layout
        props = context.object.dts_shape
        if props.migration_note:
            box = layout.box()
            box.alert = True
            column = box.column(align=True)
            for line in props.migration_note.split("; "):
                column.label(text=line, icon="ERROR")
            box.operator(DTS_OT_dismiss_migration_note.bl_idname, icon="X")
        layout.label(text=f"{len(props.details)} detail level(s), "
                          f"{len(props.material_order)} material(s)")


class OBJECT_PT_dts_details(Panel):
    bl_label = "Detail Levels"
    bl_parent_id = "OBJECT_PT_dts_shape"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    @classmethod
    def poll(cls, context):
        return _is_shape(context.object)

    def draw(self, context):
        props = context.object.dts_shape
        _table(self.layout, props, "details", "DTS_UL_details", "object.dts_shape.details")
        if 0 <= props.details_index < len(props.details):
            item = props.details[props.details_index]
            column = self.layout.column(align=True)
            column.prop(item, "size")
            column.prop(item, "sub_shape_num")
            column.prop(item, "object_detail_num")
            sub = column.column(align=True)
            sub.enabled = False  # LOD metrics the add-on cannot recompute
            sub.prop(item, "average_error")
            sub.prop(item, "max_error")
            sub.prop(item, "poly_count")


class OBJECT_PT_dts_materials(Panel):
    bl_label = "Material Order"
    bl_parent_id = "OBJECT_PT_dts_shape"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _is_shape(context.object)

    def draw(self, context):
        props = context.object.dts_shape
        self.layout.label(text="Map slots and IFL entries index into this list")
        _table(
            self.layout, props, "material_order", "DTS_UL_material_order",
            "object.dts_shape.material_order", move=True,
        )
        if 0 <= props.material_order_index < len(props.material_order):
            self.layout.prop(props.material_order[props.material_order_index], "material")


class MATERIAL_PT_dts_ifl(Panel):
    bl_label = "IFL Frames"
    bl_parent_id = "MATERIAL_PT_dts_material"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "material"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return getattr(context, "material", None) is not None

    def draw(self, context):
        mat = context.material
        props = mat.dts_material
        layout = self.layout
        layout.prop(props, "is_ifl")
        if not props.is_ifl:
            layout.label(text="An IFL material flips through a .ifl", icon="INFO")
            return
        layout.label(text=f"Writes {materials.ifl_filename(mat)} beside the .dts")
        _table(layout, props, "ifl_frames", "DTS_UL_ifl_frames",
               "material.dts_material.ifl_frames")
        if 0 <= props.ifl_frames_index < len(props.ifl_frames):
            frame = props.ifl_frames[props.ifl_frames_index]
            column = layout.column(align=True)
            column.template_ID(frame, "image", open="image.open")
            column.prop(frame, "duration")
        row = layout.row()
        row.operator(DTS_OT_refresh_ifl.bl_idname, icon="FILE_REFRESH")


class OBJECT_PT_dts_names(Panel):
    bl_label = "Name Table"
    bl_parent_id = "OBJECT_PT_dts_shape"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _is_shape(context.object)

    def draw(self, context):
        self.layout.label(text="Order is load-bearing: every name index is an offset here")
        _table(
            self.layout, context.object.dts_shape, "names", "DTS_UL_names",
            "object.dts_shape.names", move=True,
        )


class OBJECT_PT_dts_decal(Panel):
    """A decal, which is now entirely this empty.

    Everything here used to be an ID custom property spread across the decal's
    mesh objects, so none of it could be changed without typing exact names
    into the Custom Properties panel.  Depth and Coverage never existed at all:
    they decide which faces the exported decal names, and the file has no
    stored answer for them to be recovered from.
    """

    bl_label = "DTS Decal"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == "EMPTY" and obj.dts_decal.is_dts

    def draw(self, context):
        from .operators import DTS_OT_rebuild_decal_preview, DTS_OT_refresh_decal

        props = context.object.dts_decal
        layout = self.layout

        column = layout.column(align=True)
        column.prop(props, "decal_name")
        column.prop(props, "index")

        column = layout.column(align=True)
        column.prop(props, "target")
        column.prop(props, "material")
        # the branch is built once and then left alone, so changing the
        # material above -- or opening a scene built by an older version --
        # needs this to show
        column.operator(
            DTS_OT_rebuild_decal_preview.bl_idname, icon="NODETREE"
        ).all_decals = False

        box = layout.box()
        box.label(text="Covered Faces", icon="FACESEL")
        box.prop(props, "rule")
        box.prop(props, "depth")
        box.prop(props, "max_angle")
        box.operator(DTS_OT_refresh_decal.bl_idname, icon="FILE_REFRESH")
        box.label(
            text="Recomputed on export; the file's own list is not kept",
            icon="INFO",
        )


class BONE_PT_dts_node(Panel):
    bl_label = "DTS Node"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "bone"

    @classmethod
    def poll(cls, context):
        return context.bone is not None and _is_shape(context.object)

    def draw(self, context):
        bone = context.bone
        props = bone.dts_node
        layout = self.layout
        if "dts_name" in bone:
            layout.label(text=f"DTS name: {bone['dts_name']}")
        layout.prop(props, "use_stored")
        column = layout.column(align=True)
        column.enabled = props.use_stored
        column.prop(props, "stored_rotation")
        column.prop(props, "stored_translation")
        if props.use_stored:
            layout.label(
                text="Export keeps these while the bone still agrees with them",
                icon="INFO",
            )


class OBJECT_PT_dts_mesh(Panel):
    """The per-mesh settings, which have no natural home in Blender's own UI.

    Every flag is drawn whether or not it is set.  They used to be ID
    properties written only when the bit was on, so a mesh that was not
    already a billboard had no billboard checkbox to tick -- the flag could be
    cleared and never set.
    """

    bl_label = "DTS Mesh"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == "MESH" and "dts_object_name" in obj

    def draw(self, context):
        obj = context.object
        props = obj.dts_mesh
        layout = self.layout

        row = layout.row()
        row.enabled = False
        row.label(text=f"object {obj['dts_object_name']}, detail "
                       f"{obj.get('dts_detail_size', '?')}")

        column = layout.column(align=True)
        column.prop(props, "billboard")
        sub = column.column(align=True)
        # the Z variant modifies the first rather than replacing it
        sub.enabled = props.billboard
        sub.prop(props, "billboard_z")
        if props.billboard:
            layout.label(text="Nothing in the viewport turns it to face you", icon="INFO")

        column = layout.column(align=True)
        column.prop(props, "has_detail_texture")
        column.prop(props, "use_encoded_normals")
        column.prop(props, "echo_type_bits")

        box = layout.box()
        box.label(text="Draw order")
        box.prop(props, "sorted_mode", text="")
        # Standard plus a blended material still exports sorted.  Say so here
        # rather than storing the mode on the mesh, which would be a second
        # source for something the material already decides.
        promoted = props.sorted_mode == "NONE" and materials.is_translucent(obj)
        if promoted:
            box.label(text="Sorted (BSP) on export: the material blends", icon="INFO")
        if props.sorted_mode == "BSP" or promoted:
            box.prop(props, "sorted_depth")
            box.label(text="Each level doubles the index buffer", icon="INFO")
        if props.sorted_mode != "NONE" or promoted:
            box.prop(props, "always_write_depth")

        box = layout.box()
        box.label(text="Decals")
        box.operator(DTS_OT_add_decal.bl_idname, icon="MOD_UVPROJECT")
        box.label(text="Select the faces to cover first", icon="INFO")

        from ..mapping import matframes

        extra = matframes.frame_count(obj.data) - 1
        if extra > 0:
            layout.label(
                text=f"{extra} extra material frame(s) in mesh attributes",
                icon="TEXTURE",
            )
        if "dts_merge_indices" in obj:
            layout.label(
                text=f"{len(obj['dts_merge_indices'])} merge index(es) (legacy LOD morph)"
            )


# the flags word, in the order the format defines it; the three blend bits are
# not here because the shader owns them (mapping/materials.py)
_MATERIAL_FLAGS = (
    ("dts_s_wrap", "S Wrap"),
    ("dts_t_wrap", "T Wrap"),
    ("dts_self_illuminating", "Self Illuminating"),
    ("dts_never_env_map", "Never Env-Map"),
    ("dts_no_mip_map", "No Mip-Map"),
    ("dts_mip_map_zero_border", "Mip-Map Zero Border"),
    ("dts_ifl_frame", "IFL Frame"),
    ("dts_detail_map_only", "Detail Map Only"),
    ("dts_bump_map_only", "Bump Map Only"),
    ("dts_reflectance_map_only", "Reflectance Map Only"),
)


class MATERIAL_PT_dts_material(Panel):
    bl_label = "DTS Material"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "material"

    @classmethod
    def poll(cls, context):
        # any material, not just an imported one: a material built in a fresh
        # scene has to be able to reach the reflectance packing, or it can be
        # turned off and never on
        return getattr(context, "material", None) is not None

    def draw(self, context):
        mat = context.material
        layout = self.layout
        layout.label(text=f"DTS name: {mat.get('dts_name') or mat.name}")

        column = layout.column(align=True)
        column.label(text="Flags")
        grid = column.grid_flow(columns=2, even_columns=True, align=True)
        for prop, label in _MATERIAL_FLAGS:
            if prop in mat:
                grid.prop(mat, f'["{prop}"]', text=label, toggle=False)
            else:
                row = grid.row()
                row.enabled = False
                row.label(text=f"{label} (unset)")

        # computed at draw time rather than read from a prop: the graph is
        # where these three live, and a stored copy would be a second source
        blend = materials.blend_flags_from_material(mat)
        box = layout.box()
        box.label(text="Blend mode comes from the shader, not from here")
        names = [
            name for name, bit in (
                ("Translucent", MAT_TRANSLUCENT),
                ("Additive", MAT_ADDITIVE),
                ("Subtractive", MAT_SUBTRACTIVE),
            ) if blend & bit
        ]
        box.label(text=", ".join(names) if names else "Opaque")

        _draw_reflectance(layout, mat)

        column = layout.column(align=True)
        column.label(text="Maps (no preview; the Principled shader ignores them)")
        for prop in ("dts_reflectance_map", "dts_bump_map", "dts_detail_map",
                     "dts_detail_scale"):
            if prop in mat:
                column.prop(mat, f'["{prop}"]', text=prop.removeprefix("dts_"))


def _draw_reflectance(layout, mat) -> None:
    """The reflectance map, its amount, and how it will be packed.

    The image slot is drawn on the *node*, not on a property of the material.
    A PointerProperty beside the node would be a second place the same fact
    lives, and the two would disagree the first time someone edited the graph;
    this way the panel and the node editor are two views of one value.
    """
    from ..mapping import envmap

    box = layout.box()
    box.label(text="Reflectance (environment map)")

    group = envmap.group_node(mat)
    node = materials.reflectance_image_node(mat)

    if group is None and node is not None:
        # imported before the env-map preview existed: it still exports, it
        # just does not show what the engine draws
        box.label(text=f"From {node.image.name}, on Metallic", icon="TEXTURE")
        box.operator(
            DTS_OT_rebuild_env_map.bl_idname, icon="NODETREE"
        ).all_materials = False
    elif group is None:
        box.operator(DTS_OT_add_reflectance.bl_idname, icon="ADD")
        box.label(text="No reflectance map; this material does not reflect", icon="INFO")
    else:
        source = envmap.mask_socket(mat)
        owner = source.node if source is not None else None
        if owner is not None and owner.type == "TEX_IMAGE":
            box.template_ID(owner, "image", new="image.new", open="image.open")
            if source.name == "Alpha":
                box.label(
                    text="The mask is this texture's alpha, shared with transparency",
                    icon="INFO",
                )
        else:
            box.label(text="The mask comes from the node graph", icon="NODETREE")
        box.prop(mat.dts_material, "reflection_amount")
        box.operator(DTS_OT_remove_reflectance.bl_idname, icon="X")

    box.prop(mat.dts_material, "reflectance_packing")
    if mat.dts_material.reflectance_packing == "DEFAULT":
        box.label(
            text="Combine Diffuse and Reflectance, in the export dialog",
            icon="EXPORT",
        )
    if group is not None and not _has_environment_image():
        box.label(text="No environment map set; see Scene Properties", icon="ERROR")


def _has_environment_image() -> bool:
    scene = bpy.context.scene
    props = getattr(scene, "dts_scene", None)
    return props is not None and props.env_map_image is not None


class SCENE_PT_dts_environment(Panel):
    """Preview settings, and the only ones this add-on has.

    Neither value is in a `.dts`.  The engine's environment map is the mission
    sky's (``engine/terrain/sky.h:227``) and the strength is set by whatever is
    drawing the shape -- 1.0 for everything the game renders.  So they are
    scene state, they are never exported, and a shape moved to another mission
    reflects that mission instead, which is what the engine does too.
    """

    bl_label = "DTS Environment Map"
    bl_idname = "SCENE_PT_dts_environment"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    @classmethod
    def poll(cls, context):
        # unconditional in Blender, where the Scene tab always has one; the
        # check is for the callers that hand draw() a stand-in context
        return getattr(context, "scene", None) is not None

    def draw(self, context):
        layout = self.layout
        props = context.scene.dts_scene

        layout.template_ID(props, "env_map_image", new="image.new", open="image.open")
        layout.prop(props, "env_map_strength")
        column = layout.column(align=True)
        column.label(text="Preview only; never written to a .dts", icon="INFO")
        column.label(text="The engine takes it from the mission sky's .dml")
        layout.operator(
            DTS_OT_rebuild_env_map.bl_idname, text="Rebuild All Materials", icon="NODETREE"
        ).all_materials = True


def _action_in_context(context):
    """Which action the sequence panels are about.

    The same rule mapping/nla.py plays by: an NLA strip if one is selected,
    otherwise whatever the object is holding.
    """
    strip = getattr(context, "active_nla_strip", None)
    if strip is not None and strip.action is not None:
        return strip.action
    obj = context.object
    if obj is not None and obj.animation_data is not None:
        return obj.animation_data.action
    return None


def draw_sequence(layout, action) -> None:
    props = action.dts_sequence_props
    layout.label(text=action.name, icon="ACTION")

    column = layout.column(align=True)
    for name in ("dts_duration", "dts_priority", "dts_tool_begin"):
        if name in action:
            column.prop(action, f'["{name}"]', text=name.removeprefix("dts_"))
    row = layout.row(align=True)
    for name in ("dts_cyclic", "dts_blend", "dts_makepath"):
        if name in action:
            row.prop(action, f'["{name}"]', text=name.removeprefix("dts_"), toggle=True)

    layout.prop(props, "scale_mode")
    if props.scale_mode == "ARBITRARY":
        layout.label(text="Arbitrary scale is refused on export", icon="ERROR")

    box = layout.box()
    box.label(text="Triggers")
    _table(box, props, "triggers", "DTS_UL_triggers", "action.triggers")
    if 0 <= props.triggers_index < len(props.triggers):
        item = props.triggers[props.triggers_index]
        column = box.column(align=True)
        column.prop(item, "state")
        column.prop(item, "pos")
        column.prop(item, "on")
        column.prop(item, "invert_on_reverse")

    box = layout.box()
    box.label(text="Ground Frames")
    box.label(text="Root motion: what gives a movement animation its speed")
    _table(box, props, "ground", "DTS_UL_ground", "action.ground")
    if 0 <= props.ground_index < len(props.ground):
        item = props.ground[props.ground_index]
        column = box.column(align=True)
        column.prop(item, "translation")
        column.prop(item, "rotation")

    # unconditionally, like ground frames and triggers: the add button lives
    # inside the box, so drawing it only when non-empty meant the first entry
    # could never be added from the UI at all
    box = layout.box()
    box.label(text="IFL Materials Advanced")
    _table(box, props, "ifl_matters", "DTS_UL_ifl_matters", "action.ifl_matters")
    if 0 <= props.ifl_matters_index < len(props.ifl_matters):
        box.prop(props.ifl_matters[props.ifl_matters_index], "material")


class _SequencePanel:
    bl_label = "DTS Sequence"
    bl_region_type = "UI"
    bl_category = "DTS"

    @classmethod
    def poll(cls, context):
        action = _action_in_context(context)
        return action is not None and bool(action.get("dts_sequence"))

    def draw(self, context):
        draw_sequence(self.layout, _action_in_context(context))


class DOPESHEET_PT_dts_sequence(_SequencePanel, Panel):
    bl_space_type = "DOPESHEET_EDITOR"
    bl_idname = "DOPESHEET_PT_dts_sequence"


class NLA_PT_dts_sequence(_SequencePanel, Panel):
    bl_space_type = "NLA_EDITOR"
    bl_idname = "NLA_PT_dts_sequence"


CLASSES = (
    OBJECT_PT_dts_shape,
    OBJECT_PT_dts_details,
    OBJECT_PT_dts_materials,
    OBJECT_PT_dts_names,
    OBJECT_PT_dts_mesh,
    OBJECT_PT_dts_decal,
    BONE_PT_dts_node,
    SCENE_PT_dts_environment,
    MATERIAL_PT_dts_material,
    MATERIAL_PT_dts_ifl,
    DOPESHEET_PT_dts_sequence,
    NLA_PT_dts_sequence,
)

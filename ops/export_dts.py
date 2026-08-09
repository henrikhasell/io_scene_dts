from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from ..dtslib import DtsWriteError, fit_to_version, write_shape_file
from ..mapping.blender_to_shape import ExportError, blender_to_shape
from ..mapping.texture_io import MAX_TEXTURE_SIZE, write_textures


class ExportDTS(bpy.types.Operator, ExportHelper):
    """Export a Torque DTS shape"""

    bl_idname = "io_scene_dts.export_dts"
    bl_label = "Export DTS"
    bl_options = {"REGISTER"}

    filename_ext = ".dts"
    filter_glob: StringProperty(default="*.dts", options={"HIDDEN"})

    version: EnumProperty(
        name="Format Version",
        items=[
            ("24", "Torque (v24)", "Newest format: Torque Game Engine 1.5.  The only "
             "version with somewhere to keep ground frames"),
            ("23", "Tribes 2 (v23)", "The format Tribes 2's own shapes use.  No ground "
             "frames -- v23 and v22 have nowhere for them"),
            ("22", "Tribes 2, older (v22)", "The bulk of Tribes 2's shapes.  Skins live "
             "with the other meshes from here up"),
            ("21", "v21", "One node track per node: a node that rotates also stores a "
             "translation.  No scale animation, and no encoded normals.  Ground frames "
             "ride at the end of the node array"),
            ("20", "v20", "As v21, without per-material reflection amounts"),
            ("19", "v19", "As v20; decals carry the empty mesh header they had when "
             "decals were derived from meshes"),
            ("18", "v18", "The flat-stream format: no mesh bounds (the engine computes "
             "them), no vertex sharing between detail levels, no merge indices, no LOD "
             "error metrics, and no decal projection planes"),
            ("17", "v17", "As v18, with 32-bit primitive and index fields"),
            ("16", "v16", "As v17, and animation is stored keyframe-major through a "
             "keyframe table"),
            ("15", "v15", "The oldest this add-on writes.  As v16, and meshes are "
             "indexed through a separate list rather than a null-mesh marker"),
        ],
        default="24",
    )
    selected_only: BoolProperty(
        name="Selected Objects Only",
        description="Export only selected mesh objects (the active armature is always used)",
        default=False,
    )
    export_sequences: BoolProperty(
        name="Export Sequences",
        description="Export DTS-tagged actions as animation sequences",
        default=True,
    )
    export_textures: BoolProperty(
        name="Export Textures",
        description="Write a .png beside the .dts for every texture the shape names, "
        "whether it was made in Blender or loaded from disk.  Existing files are "
        "overwritten, so exporting into a game's textures/ tree rewrites the art "
        "there.  A material's .ifl is written either way -- it is animation data, "
        "not art",
        default=True,
    )
    decals_as_meshes: BoolProperty(
        name="Export Decals as Meshes",
        description="Write each decal as an ordinary mesh object -- the faces it "
        "covers, copied, lifted just off the surface so the two do not z-fight, "
        "with the projection baked into its UVs -- instead of as a TSDecalMesh.  "
        "Anything that reads "
        "a .dts then draws the decal, including tools that skip the decal "
        "section, and the shape no longer needs a translucent mesh to draw "
        "decals against.  The file carries no decal entries afterwards, so "
        "re-importing gives meshes and no projectors",
        default=False,
    )
    combine_reflectance: BoolProperty(
        name="Combine Diffuse and Reflectance",
        description="Write each material's reflectance map into its diffuse "
        "texture's alpha channel, so the shape names one texture and points its "
        "reflectance slot at itself.  That is the packing every material in "
        "Tribes 2's own shapes uses.  Unchecked, the diffuse and the reflectance "
        "are written as separate files and the reflectance gets its own entry in "
        "the material list.  A material set to Combine or Separate in its DTS "
        "Material panel overrules this for itself",
        default=True,
    )
    scale_textures_pot: BoolProperty(
        name="Scale Textures to Power of Two",
        description="Resize exported textures to the nearest power-of-two "
        "dimensions (100x60 becomes 128x64).  Torque's texture loader assumes "
        "power-of-two sizes, so one that is not renders white or garbled in-game "
        "while looking correct in Blender.  Only the written file is resized -- "
        "the image in your .blend is left alone",
        default=True,
    )
    limit_texture_size: BoolProperty(
        name="Limit Textures to 512x512",
        description="Scale any exported texture larger than 512x512 down to fit, "
        "keeping its aspect ratio.  An oversized texture still renders, but the "
        "engine uploads and mipmaps all of it and the driver resamples what its "
        "hardware cannot hold, so the size is paid for and then discarded.  "
        "512 is the largest the art this format ships with is built to.  Only "
        "the written file is resized -- the image in your .blend is left alone",
        default=True,
    )

    def execute(self, context):
        arm_obj = context.active_object
        if arm_obj is not None and arm_obj.type != "ARMATURE":
            arm_obj = next((o for o in context.selected_objects if o.type == "ARMATURE"), None)
        if arm_obj is None:
            arm_obj = next((o for o in context.scene.objects if o.type == "ARMATURE"), None)

        try:
            shape, texture_writes, warnings = blender_to_shape(
                context,
                arm_obj,
                selected_only=self.selected_only,
                do_export_sequences=self.export_sequences,
                combine_reflectance=self.combine_reflectance,
                decals_as_meshes=self.decals_as_meshes,
            )
        except ExportError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        version = int(self.version)
        # what the chosen version cannot store is dropped, not refused: the
        # user picked the version because the engine they target needs it, and
        # no amount of editing would make ground frames fit in v23.  It is
        # still a loss, so it is a warning and never silent
        warnings.extend(fit_to_version(shape, version))
        try:
            write_shape_file(shape, self.filepath, version)
        except DtsWriteError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        # after the shape, and only after: a texture beside a .dts that failed
        # to write would be litter, and overwriting art for a shape that never
        # got written would be worse than litter
        # unconditional: the checkbox gates images, not the generated .ifl
        # sidecars, which are shape data the .dts names by filename
        written = write_textures(
            texture_writes, Path(self.filepath).parent, warnings,
            include_images=self.export_textures,
            power_of_two=self.scale_textures_pot,
            max_size=MAX_TEXTURE_SIZE if self.limit_texture_size else None,
        )

        for w in warnings:
            self.report({"WARNING"}, w)
        textures = f", {written} texture(s)" if written else ""
        self.report({"INFO"}, f"Exported v{version}: {len(shape.nodes)} nodes, "
                              f"{len(shape.objects)} objects, "
                              f"{len(shape.sequences)} sequences{textures}")
        return {"FINISHED"}


def menu_func(self, context):
    self.layout.operator(ExportDTS.bl_idname, text="Torque Shape (.dts)")

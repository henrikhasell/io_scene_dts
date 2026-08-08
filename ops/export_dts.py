from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from ..dtslib import DtsWriteError, strip_ground_frames, write_shape_file
from ..mapping.blender_to_shape import ExportError, blender_to_shape
from ..mapping.texture_io import write_textures


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
            ("24", "Torque (v24)", "Newest format: Torque Game Engine 1.5 (supports ground frames)"),
            ("23", "Tribes 2 (v23)", "The format Tribes 2's own shapes use (no ground frames)"),
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
    drop_ground_frames: BoolProperty(
        name="Strip Ground Frames",
        description="Remove ground-frame data so the shape fits in v23 "
        "(movement animations lose their ground speed!)",
        default=False,
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
    scale_textures_pot: BoolProperty(
        name="Scale Textures to Power of Two",
        description="Resize exported textures to the nearest power-of-two "
        "dimensions (100x60 becomes 128x64).  Torque's texture loader assumes "
        "power-of-two sizes, so one that is not renders white or garbled in-game "
        "while looking correct in Blender.  Only the written file is resized -- "
        "the image in your .blend is left alone",
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
            )
        except ExportError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        version = int(self.version)
        if self.drop_ground_frames and version == 23:
            strip_ground_frames(shape)
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

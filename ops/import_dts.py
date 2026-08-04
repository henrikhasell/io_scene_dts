from pathlib import Path

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from ..dtslib import DtsError, read_shape_file
from ..mapping.shape_to_blender import shape_to_blender


class ImportDTS(bpy.types.Operator, ImportHelper):
    """Import a Torque DTS shape"""

    bl_idname = "io_scene_dts.import_dts"
    bl_label = "Import DTS"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".dts"
    filter_glob: StringProperty(default="*.dts", options={"HIDDEN"})

    import_sequences: BoolProperty(
        name="Import Sequences",
        description="Create an Action for every animation sequence",
        default=True,
    )
    create_materials: BoolProperty(
        name="Create Materials",
        description="Create Principled BSDF materials with textures found next to the file",
        default=True,
    )

    def execute(self, context):
        try:
            shape = read_shape_file(self.filepath)
        except DtsError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        name = Path(self.filepath).stem
        try:
            arm_obj, warnings = shape_to_blender(
                shape,
                name,
                context,
                filepath=self.filepath,
                do_import_sequences=self.import_sequences,
                create_materials=self.create_materials,
            )
        except DtsError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        for w in warnings:
            self.report({"WARNING"}, w)
        self.report({"INFO"}, f"Imported {name}: {len(shape.nodes)} nodes, "
                              f"{len(shape.objects)} objects, {len(shape.sequences)} sequences")
        return {"FINISHED"}


def menu_func(self, context):
    self.layout.operator(ImportDTS.bl_idname, text="Torque Shape (.dts)")

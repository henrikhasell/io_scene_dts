from pathlib import Path

import bpy
from bpy.props import BoolProperty, CollectionProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from ..dtslib import DtsError, read_dsq, read_shape_file
from ..mapping.dsq import dsq_to_actions
from ..mapping.nla import scene_fps, stack_actions
from ..mapping.shape_to_blender import shape_to_blender


class ImportDTS(bpy.types.Operator, ImportHelper):
    """Import a Torque DTS shape"""

    bl_idname = "io_scene_dts.import_dts"
    bl_label = "Import DTS"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".dts"
    filter_glob: StringProperty(default="*.dts;*.dsq", options={"HIDDEN"})

    # multi-select: one .dts plus any number of .dsq companions
    files: CollectionProperty(
        type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"}
    )
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})

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

    def _selected_paths(self):
        """The file browser's multi-selection, or just filepath when the
        operator is driven from a script."""
        names = [f.name for f in self.files if f.name] if self.files else []
        if names and self.directory:
            return [Path(self.directory) / n for n in names]
        return [Path(self.filepath)]

    def _apply_companions(self, paths, arm_obj, context):
        """Load .dsq files selected next to the shape onto its armature.

        Returns (sequences, files_read) — a file that fails to read is skipped,
        so the counts differ and the report should say so.
        """
        collected, read = [], 0
        for path in paths:
            try:
                dsq = read_dsq(path.read_bytes())
            except (OSError, DtsError) as e:
                self.report({"WARNING"}, f"{path.name}: {e}")
                continue
            read += 1
            actions, warnings = dsq_to_actions(dsq, arm_obj)
            for w in warnings:
                self.report({"WARNING"}, f"{path.name}: {w}")
            collected += actions

        _, skipped = stack_actions(arm_obj, collected, scene_fps(context))
        for action in skipped:
            self.report({"WARNING"}, f"{action.name}: no evaluable channels; no NLA strip created")
        return len(collected), read

    def execute(self, context):
        paths = self._selected_paths()
        shapes = [p for p in paths if p.suffix.lower() == ".dts"]
        companions = sorted(p for p in paths if p.suffix.lower() == ".dsq")
        if len(shapes) != 1:
            self.report(
                {"ERROR"},
                f"select exactly one .dts (got {len(shapes)}); any .dsq files "
                "selected with it are loaded onto its armature",
            )
            return {"CANCELLED"}
        dts_path = shapes[0]

        try:
            shape = read_shape_file(str(dts_path))
        except DtsError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        name = dts_path.stem
        try:
            arm_obj, warnings = shape_to_blender(
                shape,
                name,
                context,
                filepath=str(dts_path),
                do_import_sequences=self.import_sequences,
                create_materials=self.create_materials,
            )
        except DtsError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        for w in warnings:
            self.report({"WARNING"}, w)

        summary = (f"Imported {name}: {len(shape.nodes)} nodes, "
                   f"{len(shape.objects)} objects, {len(shape.sequences)} sequences")
        if companions:
            extra, read = self._apply_companions(companions, arm_obj, context)
            summary += f"; +{extra} sequence(s) from {read} of {len(companions)} DSQ file(s)"
        self.report({"INFO"}, summary)
        return {"FINISHED"}


def menu_func(self, context):
    self.layout.operator(ImportDTS.bl_idname, text="Torque Shape (.dts)")

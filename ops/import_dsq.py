import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper

from ..dtslib import DtsError, read_dsq
from ..mapping.dsq import dsq_to_actions
from ..mapping.nla import scene_fps, stack_actions


class ImportDSQ(bpy.types.Operator, ImportHelper):
    """Import a Torque DSQ sequence file onto the active armature"""

    bl_idname = "io_scene_dts.import_dsq"
    bl_label = "Import DSQ"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".dsq"
    filter_glob: StringProperty(default="*.dsq", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == "ARMATURE"

    def execute(self, context):
        arm_obj = context.active_object
        if arm_obj is None or arm_obj.type != "ARMATURE":
            self.report({"ERROR"}, "select the target armature first")
            return {"CANCELLED"}
        try:
            with open(self.filepath, "rb") as f:
                dsq = read_dsq(f.read())
        except (OSError, DtsError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        actions, warnings = dsq_to_actions(dsq, arm_obj)
        for w in warnings:
            self.report({"WARNING"}, w)

        # what you just imported is what you want to see playing
        tracks, skipped = stack_actions(
            arm_obj, actions, scene_fps(context),
            keep_playing=actions[0].name if actions else None,
        )
        for action in skipped:
            self.report({"WARNING"}, f"{action.name}: no evaluable channels; no NLA strip created")

        self.report(
            {"INFO"},
            f"Imported {len(actions)} sequence(s) as {len(tracks)} NLA track(s): "
            + ", ".join(a.name for a in actions),
        )
        return {"FINISHED"}


def menu_func(self, context):
    self.layout.operator(ImportDSQ.bl_idname, text="Torque Sequence (.dsq)")

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from ..dtslib import DtsError, write_dsq
from ..mapping.dsq import actions_to_dsq
from ..mapping.nla import playing_action


class ExportDSQ(bpy.types.Operator, ExportHelper):
    """Export actions of the active armature as a Torque DSQ sequence file"""

    bl_idname = "io_scene_dts.export_dsq"
    bl_label = "Export DSQ"
    bl_options = {"REGISTER"}

    filename_ext = ".dsq"
    filter_glob: StringProperty(default="*.dsq", options={"HIDDEN"})

    active_action_only: BoolProperty(
        name="Active Sequence Only",
        description=(
            "Export only the sequence currently playing — the armature's one "
            "unmuted NLA track — instead of all DTS actions"
        ),
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == "ARMATURE"

    def execute(self, context):
        arm_obj = context.active_object
        if arm_obj is None or arm_obj.type != "ARMATURE":
            self.report({"ERROR"}, "select the source armature first")
            return {"CANCELLED"}

        if self.active_action_only:
            # sequences import as NLA strips, so "active" is the one unmuted
            # track rather than an assigned action
            action = playing_action(arm_obj)
            if action is None:
                self.report(
                    {"ERROR"},
                    "no single active sequence — leave exactly one NLA track unmuted",
                )
                return {"CANCELLED"}
            actions = [action]
        else:
            actions = [a for a in bpy.data.actions if a.get("dts_sequence")]
            if not actions:
                actions = [a for a in bpy.data.actions]
        if not actions:
            self.report({"ERROR"}, "no actions to export")
            return {"CANCELLED"}

        dsq, warnings = actions_to_dsq(arm_obj, actions)
        try:
            data = write_dsq(dsq)
            with open(self.filepath, "wb") as f:
                f.write(data)
        except (OSError, DtsError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        for w in warnings:
            self.report({"WARNING"}, w)
        self.report({"INFO"}, f"Exported {len(dsq.sequences)} sequence(s)")
        return {"FINISHED"}


def menu_func(self, context):
    self.layout.operator(ExportDSQ.bl_idname, text="Torque Sequence (.dsq)")

"""Stack imported DTS sequences as NLA strips, each at its own frame rate.

A Torque sequence carries its own playback time (``dts_duration``), and one
shape's sequences rarely agree: light_male's body animations run at 15 fps
while its jetpack flare runs at 30.  The importer lays keyframes one per
Blender frame, so no single ``scene.render.fps`` can play them all at the
right speed.  An NLA strip has a per-strip ``scale``, which can.

This only builds strips.  ``actions_to_dsq`` still takes duration from
``dts_duration``, so stacking (or later dragging a strip) cannot change what
gets exported.
"""

import bpy
from bpy.props import BoolProperty

from ..mapping.sequences import _iter_fcurves


def strip_scale(action, fps: float) -> float:
    """Frames-per-frame factor that plays the action over its stored duration.

    Keyframe *i* sits on Blender frame *i*+1, so the action spans
    ``num_keyframes - 1`` frames; it should span ``duration * fps``.
    """
    n = int(action.get("dts_keyframes") or 0)
    duration = float(action.get("dts_duration") or 0.0)
    if n > 1 and duration > 0.0:
        return duration * fps / (n - 1)
    return 1.0


def _targets(action, arm_obj) -> bool:
    """True when the action drives a bone this armature actually has."""
    bones = arm_obj.data.bones
    for fc in _iter_fcurves(action):
        if fc.data_path.startswith('pose.bones["') and fc.data_path.split('"')[1] in bones:
            return True
    return False


def _stacked_actions(anim_data) -> set:
    return {
        strip.action.name
        for track in anim_data.nla_tracks
        for strip in track.strips
        if strip.action is not None
    }


class StackSequencesNLA(bpy.types.Operator):
    """Put every imported DTS sequence on its own NLA track, retimed to the
    playback speed stored in the shape"""

    bl_idname = "io_scene_dts.stack_sequences_nla"
    bl_label = "Stack DTS Sequences in NLA"
    bl_options = {"REGISTER", "UNDO"}

    mute_tracks: BoolProperty(
        name="Mute Stacked Tracks",
        description=(
            "Leave a single track playing.  The NLA evaluates every unmuted "
            "track at once, which is what you want for a flare over a walk "
            "cycle but not for forty alternative body animations"
        ),
        default=True,
    )
    set_frame_range: BoolProperty(
        name="Set Frame Range",
        description="Extend the scene's frame range to cover the longest sequence",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "ARMATURE"

    def execute(self, context):
        arm_obj = context.active_object
        anim = arm_obj.animation_data or arm_obj.animation_data_create()
        scene = context.scene
        fps = scene.render.fps / scene.render.fps_base

        sequences = [a for a in bpy.data.actions if a.get("dts_sequence")]
        candidates = [a for a in sequences if _targets(a, arm_obj)]
        # visibility/decal-only sequences carry no pose fcurves, so a strip would
        # evaluate nothing; say so rather than dropping them quietly
        for action in sequences:
            if action not in candidates:
                self.report(
                    {"WARNING"},
                    f"{action.name}: no bone channels (visibility/decal only); not stacked",
                )
        if not candidates:
            self.report({"ERROR"}, "no imported DTS sequences target this armature")
            return {"CANCELLED"}

        # whichever sequence is playing now stays the one that plays; a strip and
        # an assigned action would otherwise both evaluate
        active = anim.action
        anim.action = None

        already = _stacked_actions(anim)
        stacked, unmuted, end = [], None, 1.0
        for action in sorted(candidates, key=lambda a: a.name.lower()):
            if action.name in already:
                continue
            track = anim.nla_tracks.new()
            track.name = action.name
            strip = track.strips.new(action.name, 1, action)
            # sync_length would recompute the strip from the action's own length
            # and discard the scale we are about to set
            strip.use_sync_length = False
            strip.scale = strip_scale(action, fps)
            if self.mute_tracks:
                track.mute = action is not active
                if action is active:
                    unmuted = track
            end = max(end, strip.frame_end)
            stacked.append(action)

        if not stacked:
            self.report({"INFO"}, "every sequence is already stacked")
            return {"CANCELLED"}

        # nothing was playing before, so leave one track on rather than a
        # silently empty timeline
        if self.mute_tracks and unmuted is None:
            anim.nla_tracks[len(anim.nla_tracks) - len(stacked)].mute = False

        if self.set_frame_range:
            scene.frame_start = 1
            scene.frame_end = max(2, int(round(end)))

        self.report(
            {"INFO"},
            f"Stacked {len(stacked)} sequence(s) at {fps:.2f} fps"
            + (f"; playing {active.name}" if active else ""),
        )
        return {"FINISHED"}


def menu_func(self, context):
    self.layout.operator(StackSequencesNLA.bl_idname, text="DTS Sequences (retimed)")

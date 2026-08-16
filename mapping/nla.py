"""Imported sequences live as NLA strips — one track each.

A Torque sequence carries its own playback time (``dts_duration``), and one
shape's sequences disagree: across light_male and its DSQs, 13 are authored at
30 fps and 21 at about 15.  Keyframes are laid one per Blender frame, so an
Action assigned straight to the armature plays at ``scene.render.fps`` and is
wrong for all but the sequences that happen to match it.

A strip's ``scale`` is the only per-clip retime Blender offers, so that is how
sequences arrive.  Nothing here touches ``dts_duration``: it stays the single
source of truth for export, and dragging a strip cannot change the file.
"""

import bpy

from .sequences import _iter_fcurves, _keyframe_count


def strip_scale(action: bpy.types.Action, fps: float) -> float:
    """Frames-per-frame factor that plays the action over its stored duration.

    Keyframe *i* sits on Blender frame *i*+1, so the action spans
    ``num_keyframes - 1`` frames; it should span ``duration * fps``.
    """
    n = _keyframe_count(action)
    duration = float(action.get("dts_duration") or 0.0)
    if n > 1 and duration > 0.0:
        return duration * fps / (n - 1)
    return 1.0


def has_channels(action: bpy.types.Action, arm_obj) -> bool:
    """True when this armature can evaluate anything in the action.

    Bone channels for bones it actually has, or custom properties on the object
    itself — object visibility is keyframed there so that one strip drives both
    the pose and the meshes' visibility drivers (see mapping/visibility.py).
    A sequence with neither (light_male's decal-only Damage) would be an empty
    strip.
    """
    bones = arm_obj.data.bones
    for fc in _iter_fcurves(action):
        path = fc.data_path
        if path.startswith('pose.bones["') and path.split('"')[1] in bones:
            return True
        if path.startswith('["') and path.split('"')[1] in arm_obj.keys():
            return True
    return False


def scene_fps(context) -> float:
    render = context.scene.render
    return render.fps / render.fps_base


NOTHING_PLAYING = False
"""*keep_playing* value meaning "leave every track muted".

Distinct from ``None``, which means "no preference, keep the first" — a shape
arrives inert, and a ``.dsq`` loaded onto an existing rig arrives playing.
"""


def stack_actions(arm_obj, actions, fps: float, keep_playing=NOTHING_PLAYING):
    """Give each action its own NLA track, timed to its stored duration.

    Returns ``(tracks, skipped)``.  *skipped* holds actions this armature can
    evaluate nothing from — a decal-only sequence, which a strip would render
    as an empty box.  Actions already on a track are left alone, so this is
    safe to call once per import into the same armature.
    """
    anim = arm_obj.animation_data or arm_obj.animation_data_create()
    # an assigned action evaluates on top of the whole stack, at scene fps
    anim.action = None
    existing = {s.action.name for t in anim.nla_tracks for s in t.strips if s.action}

    tracks, skipped = [], []
    for action in actions:
        if not has_channels(action, arm_obj):
            skipped.append(action)
            continue
        if action.name in existing:
            continue
        track = anim.nla_tracks.new()
        track.name = action.name
        strip = track.strips.new(action.name, 1, action)
        # sync_length recomputes the strip from the action's own length and
        # would discard the scale set below
        strip.use_sync_length = False
        strip.scale = strip_scale(action, fps)
        tracks.append(track)

    _solo(anim, keep_playing)
    return tracks, skipped


def _solo(anim, keep_playing) -> None:
    """Leave at most one track playing.

    The NLA evaluates every unmuted track at once.  That is what you want for a
    flare over a walk cycle, and meaningless for forty alternative body
    animations, so a shape's sequences arrive as a library.

    With :data:`NOTHING_PLAYING` the library arrives closed.  Sequence 0 is not
    a choice — light_male's is an eleven-frame jet-flare visibility ramp that
    holds its last value forever, so importing it "playing" leaves a flare lit
    over every animation the user goes on to pick, with nothing on screen
    saying why.  Whichever sequence a shape happens to list first is the wrong
    one to start; the user picks.
    """
    if keep_playing is NOTHING_PLAYING:
        for track in anim.nla_tracks:
            track.mute = True
        return
    names = [t.name for t in anim.nla_tracks]
    if not names:
        return
    keep = keep_playing if keep_playing in names else names[0]
    for track in anim.nla_tracks:
        track.mute = track.name != keep


def playing_action(arm_obj):
    """The sequence a user would call 'current': an assigned action if one
    somehow exists, else the single unmuted track's strip."""
    anim = arm_obj.animation_data
    if anim is None:
        return None
    if anim.action is not None:
        return anim.action
    live = [s.action for t in anim.nla_tracks if not t.mute for s in t.strips if s.action]
    return live[0] if len(live) == 1 else None

"""DTS sequences <-> Blender actions.

One Action per sequence.  Pose-bone fcurves carry node rotation/translation
keyframes (keyframe i lives on Blender frame i+1).  Everything Blender has no
natural home for rides on action custom properties:

- dts_cyclic / dts_blend / dts_makepath / dts_priority / dts_duration /
  dts_tool_begin / dts_keyframes / dts_flags
- dts_ground:  JSON [[x,y,z],[qx,qy,qz,qw]] pairs (raw Quat16 ints, lossless)
- dts_triggers: JSON [[state, pos], ...]
- dts_object_anim: JSON {object_name: {"vis": [...], "frame": [...],
  "matframe": [...]}} — per-keyframe object-state tracks
- dts_scale_anim: JSON preserving scale animation raw arrays (rare)

For Blend sequences the pose values are the raw blend offsets (the engine
post-multiplies them onto the node's local transform), not absolute poses.

Keyframe addressing in the DTS arrays is channel-major:
array[base + ordinal_in_matters_set * num_keyframes + keyframe].
"""

from __future__ import annotations

import json

import bpy
from mathutils import Matrix, Quaternion, Vector

from ..dtslib import ObjectState, Quat16, Sequence, Shape, Trigger, TSIntegerSet
from ..dtslib.types import SEQ_BLEND, SEQ_CYCLIC, SEQ_MAKE_PATH


# ----------------------------------------------------------------------
# transforms
# ----------------------------------------------------------------------


def dts_quat_to_blender(q: Quat16) -> Quaternion:
    """DTS stores the conjugate of the standard-convention quaternion."""
    x, y, z, w = q.normalized_floats()
    return Quaternion((w, -x, -y, -z))


def blender_quat_to_dts(q: Quaternion) -> Quat16:
    qn = q.normalized()
    return Quat16.from_floats(-qn.x, -qn.y, -qn.z, qn.w)


def dts_local_matrix(q: Quat16, t) -> Matrix:
    return Matrix.Translation(Vector(t)) @ dts_quat_to_blender(q).to_matrix().to_4x4()


# ----------------------------------------------------------------------
# slotted-action compatibility (Blender 4.4+ layered actions)
# ----------------------------------------------------------------------


def _action_channelbag(action: bpy.types.Action, id_obj):
    """Return the fcurve container for id_obj, creating slot/layer as needed."""
    if hasattr(action, "slots"):
        slot = action.slots.new(id_type="OBJECT", name=id_obj.name)
        layer = action.layers.new("Layer") if not action.layers else action.layers[0]
        strip = layer.strips.new(type="KEYFRAME") if not layer.strips else layer.strips[0]
        return strip.channelbag(slot, ensure=True), slot
    return action, None  # pre-4.4: Action itself owns .fcurves


def _assign_action(obj, action, slot) -> None:
    ad = obj.animation_data or obj.animation_data_create()
    ad.action = action
    if slot is not None:
        ad.action_slot = slot


def _iter_fcurves(action: bpy.types.Action):
    if hasattr(action, "layers") and action.layers:
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    yield from bag.fcurves
    else:
        yield from action.fcurves


# ----------------------------------------------------------------------
# import: Shape sequences -> Actions
# ----------------------------------------------------------------------


def import_sequences(shape: Shape, arm_obj: bpy.types.Object, bone_name_by_node: dict[int, str]) -> list[bpy.types.Action]:
    rest_local = _rest_local_matrices(shape)
    actions = []
    slots = []
    for seq in shape.sequences:
        name = shape.name(seq.name_index) or "sequence"
        action = bpy.data.actions.new(name)
        _store_sequence_props(action, seq, shape)
        bag, slot = _action_channelbag(action, arm_obj)
        slots.append(slot)

        n = seq.num_keyframes
        rot_members = sorted(seq.rotation_matters.indices())
        trans_members = sorted(seq.translation_matters.indices())
        blend = bool(seq.flags & SEQ_BLEND)

        for node in sorted(set(rot_members) | set(trans_members)):
            bone = bone_name_by_node.get(node)
            if bone is None:
                continue
            quats, locs = [], []
            for kf in range(n):
                q16 = (
                    shape.node_rotations[seq.base_rotation + seq.rotation_matters.ordinal_of(node) * n + kf]
                    if node in rot_members
                    else shape.default_rotations[node]
                )
                t = (
                    shape.node_translations[seq.base_translation + seq.translation_matters.ordinal_of(node) * n + kf]
                    if node in trans_members
                    else shape.default_translations[node]
                )
                local = dts_local_matrix(q16, t)
                basis = local if blend else rest_local[node].inverted() @ local
                quats.append(basis.to_quaternion())
                locs.append(basis.to_translation())
            # keep quaternion fcurves continuous
            for i in range(1, n):
                if quats[i].dot(quats[i - 1]) < 0:
                    quats[i].negate()
            base = f'pose.bones["{bone}"]'
            _write_fcurves(bag, f"{base}.rotation_quaternion", 4, [[q.w, q.x, q.y, q.z] for q in quats])
            _write_fcurves(bag, f"{base}.location", 3, [list(v) for v in locs])

        for i, trig in enumerate(_seq_triggers(shape, seq)):
            state_bit = trig.state & 0x3FFFFFFF
            on = bool(trig.state & (1 << 31))
            marker = action.pose_markers.new(f"trig{state_bit.bit_length() - 1 if state_bit else 0}:{'on' if on else 'off'}")
            marker.frame = 1 + round(trig.pos * max(n - 1, 0))
        actions.append(action)
    if actions:
        _assign_action(arm_obj, actions[0], slots[0])
    return actions


def _write_fcurves(bag, data_path: str, channels: int, samples) -> None:
    for ch in range(channels):
        fc = bag.fcurves.new(data_path=data_path, index=ch)
        fc.keyframe_points.add(len(samples))
        for i, sample in enumerate(samples):
            kp = fc.keyframe_points[i]
            kp.co = (i + 1, sample[ch])
            kp.interpolation = "LINEAR"
        fc.update()


def _rest_local_matrices(shape: Shape) -> list[Matrix]:
    return [
        dts_local_matrix(shape.default_rotations[i], shape.default_translations[i])
        for i in range(len(shape.nodes))
    ]


def _seq_triggers(shape: Shape, seq: Sequence) -> list[Trigger]:
    return shape.triggers[seq.first_trigger : seq.first_trigger + seq.num_triggers]


def _store_sequence_props(action: bpy.types.Action, seq: Sequence, shape: Shape) -> None:
    action["dts_sequence"] = True
    action["dts_flags"] = seq.flags
    action["dts_cyclic"] = bool(seq.flags & SEQ_CYCLIC)
    action["dts_blend"] = bool(seq.flags & SEQ_BLEND)
    action["dts_makepath"] = bool(seq.flags & SEQ_MAKE_PATH)
    action["dts_priority"] = seq.priority
    action["dts_duration"] = seq.duration
    action["dts_tool_begin"] = seq.tool_begin
    action["dts_keyframes"] = seq.num_keyframes

    n = seq.num_keyframes
    ground = []
    for i in range(seq.num_ground_frames):
        idx = seq.first_ground_frame + i
        if idx >= len(shape.ground_translations):
            # v22/23 shapes can claim ground frames they don't carry ("shapes
            # accidentally had no ground transforms"); the engine guards this
            # in TSThread::getGround, so do we
            break
        t = shape.ground_translations[idx]
        q = shape.ground_rotations[idx]
        ground.append([list(t), [q.x, q.y, q.z, q.w]])
    if ground:
        action["dts_ground"] = json.dumps(ground)

    trigs = [[t.state, t.pos] for t in _seq_triggers(shape, seq)]
    if trigs:
        action["dts_triggers"] = json.dumps(trigs)

    obj_anim = {}
    for kind, matters in (("vis", seq.vis_matters), ("frame", seq.frame_matters), ("matframe", seq.mat_frame_matters)):
        for obj_index in matters.indices():
            if obj_index >= len(shape.objects):
                continue
            base_name = shape.name(shape.objects[obj_index].name_index)
            track = []
            for kf in range(n):
                st = shape.object_states[seq.base_object_state + matters.ordinal_of(obj_index) * n + kf]
                track.append(st.vis if kind == "vis" else (st.frame_index if kind == "frame" else st.mat_frame_index))
            obj_anim.setdefault(base_name, {})[kind] = track
    if obj_anim:
        action["dts_object_anim"] = json.dumps(obj_anim)

    if seq.flags & 0x7:  # any scale
        scale = {
            "flags": seq.flags & 0x7,
            "base": seq.base_scale,
            "nodes": [shape.node_name(i) for i in seq.scale_matters.indices()],
            "uniform": shape.node_uniform_scales[seq.base_scale : seq.base_scale + seq.scale_matters.count() * n]
            if seq.animates_uniform_scale()
            else [],
            "aligned": [list(v) for v in shape.node_aligned_scales[seq.base_scale : seq.base_scale + seq.scale_matters.count() * n]]
            if seq.animates_aligned_scale()
            else [],
            "arbitrary_factors": [list(v) for v in shape.node_arbitrary_scale_factors[seq.base_scale : seq.base_scale + seq.scale_matters.count() * n]]
            if seq.animates_arbitrary_scale()
            else [],
            "arbitrary_rots": [
                [q.x, q.y, q.z, q.w]
                for q in shape.node_arbitrary_scale_rots[seq.base_scale : seq.base_scale + seq.scale_matters.count() * n]
            ]
            if seq.animates_arbitrary_scale()
            else [],
        }
        action["dts_scale_anim"] = json.dumps(scale)


# ----------------------------------------------------------------------
# export: Actions -> Shape sequences
# ----------------------------------------------------------------------


def export_sequences(
    shape: Shape,
    arm_obj: bpy.types.Object,
    actions: list[bpy.types.Action],
    node_index_by_bone: dict[str, int],
    object_index_by_name: dict[str, int],
) -> list[str]:
    """Append sequences built from actions to shape.  Returns warnings."""
    warnings = []
    rest_local = _rest_local_matrices(shape)

    for action in actions:
        n = int(action.get("dts_keyframes", 0)) or _keyframe_count(action)
        if n <= 0:
            warnings.append(f"action {action.name!r} has no keyframes; skipped")
            continue

        seq = Sequence(name_index=shape.add_name(_seq_name(action)))
        seq.num_keyframes = n
        seq.duration = float(action.get("dts_duration", n / 30.0))
        seq.priority = int(action.get("dts_priority", 0))
        seq.tool_begin = float(action.get("dts_tool_begin", 0.0))
        flags = int(action.get("dts_flags", 0)) & ~(SEQ_CYCLIC | SEQ_BLEND | SEQ_MAKE_PATH)
        if action.get("dts_cyclic"):
            flags |= SEQ_CYCLIC
        if action.get("dts_blend"):
            flags |= SEQ_BLEND
        if action.get("dts_makepath"):
            flags |= SEQ_MAKE_PATH
        seq.flags = flags
        blend = bool(flags & SEQ_BLEND)

        # collect animated bones
        bone_channels: dict[str, dict[str, dict[int, object]]] = {}
        for fc in _iter_fcurves(action):
            if not fc.data_path.startswith('pose.bones["'):
                continue
            bone = fc.data_path.split('"')[1]
            if bone not in node_index_by_bone:
                warnings.append(f"action {action.name!r}: bone {bone!r} has no DTS node; channel dropped")
                continue
            prop = fc.data_path.rsplit(".", 1)[-1]
            bone_channels.setdefault(bone, {}).setdefault(prop, {})[fc.array_index] = fc

        members = sorted(
            (node_index_by_bone[b] for b in bone_channels),
        )
        rot_set, trans_set = TSIntegerSet(), TSIntegerSet()
        for node in members:
            rot_set.set(node)
            trans_set.set(node)
        seq.rotation_matters = rot_set
        seq.translation_matters = trans_set

        seq.base_rotation = len(shape.node_rotations)
        seq.base_translation = len(shape.node_translations)
        bone_by_node = {node_index_by_bone[b]: b for b in bone_channels}
        # rotations, channel-major
        for node in members:
            chans = bone_channels[bone_by_node[node]]
            for kf in range(n):
                basis = _sample_basis(chans, kf + 1)
                local = basis if blend else rest_local[node] @ basis
                shape.node_rotations.append(blender_quat_to_dts(local.to_quaternion()))
        for node in members:
            chans = bone_channels[bone_by_node[node]]
            for kf in range(n):
                basis = _sample_basis(chans, kf + 1)
                local = basis if blend else rest_local[node] @ basis
                shape.node_translations.append(tuple(local.to_translation()))

        # ground frames
        ground = json.loads(action.get("dts_ground", "[]") or "[]")
        seq.first_ground_frame = len(shape.ground_translations)
        seq.num_ground_frames = len(ground)
        for t, q in ground:
            shape.ground_translations.append(tuple(t))
            shape.ground_rotations.append(Quat16(*q))

        # triggers
        trigs = json.loads(action.get("dts_triggers", "[]") or "[]")
        seq.first_trigger = len(shape.triggers)
        seq.num_triggers = len(trigs)
        for state, pos in trigs:
            shape.triggers.append(Trigger(int(state) & 0xFFFFFFFF, float(pos)))

        # object-state tracks
        obj_anim = json.loads(action.get("dts_object_anim", "{}") or "{}")
        vis_set, frame_set, matframe_set = TSIntegerSet(), TSIntegerSet(), TSIntegerSet()
        tracked = []
        for base_name, tracks in obj_anim.items():
            oi = object_index_by_name.get(base_name)
            if oi is None:
                warnings.append(f"action {action.name!r}: object {base_name!r} not exported; state track dropped")
                continue
            tracked.append((oi, tracks))
            if "vis" in tracks:
                vis_set.set(oi)
            if "frame" in tracks:
                frame_set.set(oi)
            if "matframe" in tracks:
                matframe_set.set(oi)
        seq.vis_matters, seq.frame_matters, seq.mat_frame_matters = vis_set, frame_set, matframe_set
        seq.base_object_state = len(shape.object_states)
        for oi, tracks in sorted(tracked):
            vis = tracks.get("vis", [1.0] * n)
            frame = tracks.get("frame", [0] * n)
            matframe = tracks.get("matframe", [0] * n)
            for kf in range(n):
                shape.object_states.append(
                    ObjectState(float(vis[kf]), int(frame[kf]), int(matframe[kf]))
                )

        # scale animation (preserved blob)
        scale = json.loads(action.get("dts_scale_anim", "{}") or "{}")
        if scale:
            seq.flags |= scale["flags"]
            sset = TSIntegerSet()
            for node_name in scale["nodes"]:
                idx = shape.find_node(node_name)
                if idx >= 0:
                    sset.set(idx)
            seq.scale_matters = sset
            if scale["flags"] & 1:
                seq.base_scale = len(shape.node_uniform_scales)
                shape.node_uniform_scales.extend(scale["uniform"])
            elif scale["flags"] & 2:
                seq.base_scale = len(shape.node_aligned_scales)
                shape.node_aligned_scales.extend(tuple(v) for v in scale["aligned"])
            else:
                seq.base_scale = len(shape.node_arbitrary_scale_factors)
                shape.node_arbitrary_scale_factors.extend(tuple(v) for v in scale["arbitrary_factors"])
                shape.node_arbitrary_scale_rots.extend(Quat16(*q) for q in scale["arbitrary_rots"])

        shape.sequences.append(seq)
    return warnings


def _seq_name(action: bpy.types.Action) -> str:
    from .naming import strip_blender_dedup

    return strip_blender_dedup(action.name)


def _keyframe_count(action: bpy.types.Action) -> int:
    last = 0.0
    for fc in _iter_fcurves(action):
        if fc.keyframe_points:
            last = max(last, fc.keyframe_points[-1].co[0])
    return int(round(last))


def _sample_basis(chans: dict[str, dict[int, object]], frame: float) -> Matrix:
    def val(prop, idx, default):
        fc = chans.get(prop, {}).get(idx)
        return fc.evaluate(frame) if fc is not None else default

    q = Quaternion(
        (
            val("rotation_quaternion", 0, 1.0),
            val("rotation_quaternion", 1, 0.0),
            val("rotation_quaternion", 2, 0.0),
            val("rotation_quaternion", 3, 0.0),
        )
    )
    t = Vector((val("location", 0, 0.0), val("location", 1, 0.0), val("location", 2, 0.0)))
    return Matrix.Translation(t) @ q.normalized().to_matrix().to_4x4()

"""DTS sequences <-> Blender actions.

One Action per sequence.  Pose-bone fcurves carry node rotation/translation
keyframes (keyframe i lives on Blender frame i+1).  Everything Blender has no
natural home for rides on action custom properties:

- dts_cyclic / dts_blend / dts_makepath / dts_priority / dts_duration /
  dts_tool_begin / dts_flags
- dts_ground:  JSON [[x,y,z],[qx,qy,qz,qw]] pairs (raw Quat16 ints, lossless)
- dts_triggers: JSON [[state, pos], ...]
- dts_scale_mode: which of the three DTS scale forms this sequence uses

Object-state and decal-state tracks are keyframed on the armature and read
back off those curves (mapping/objectstate.py); node scale animation is
keyframed as pose-bone scale.  The sequence's length and its rotation and
translation matters sets are likewise read off the curves that exist rather
than stored beside them.

For Blend sequences the pose values are the raw blend offsets (the engine
post-multiplies them onto the node's local transform), not absolute poses.

Keyframe addressing in the DTS arrays is channel-major:
array[base + ordinal_in_matters_set * num_keyframes + keyframe].
"""

from __future__ import annotations


import bpy
from mathutils import Matrix, Quaternion, Vector

from ..dtslib import ObjectState, Quat16, Sequence, Shape, Trigger, TSIntegerSet
from ..dtslib.sequence_io import object_membership
from ..dtslib.types import (
    SEQ_ALIGNED_SCALE,
    SEQ_ANY_SCALE,
    SEQ_ARBITRARY_SCALE,
    SEQ_BLEND,
    SEQ_CYCLIC,
    SEQ_MAKE_PATH,
    SEQ_UNIFORM_SCALE,
)
from .decals import decal_names_by_index, read_decal_tracks, write_decal_fcurves
from ..props.legacy import pack_trigger, parse_trigger_state
from ..props.sequence import SCHEMA_VERSION
from .ifl import material_name_for
from .objectstate import read_tracks, write_tracks


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
    """Return the fcurve container for id_obj, creating slot/layer as needed.

    The slot must be the one *id_obj is bound to*, not a fresh one.  Minting a
    slot per call put hand-written curves -- an authored vis or frame track --
    in a bag nothing evaluates: the fcurve was there, sampled correctly on
    export, and moved nothing in the viewport.
    """
    if hasattr(action, "slots"):
        anim = getattr(id_obj, "animation_data", None)
        slot = anim.action_slot if anim is not None and anim.action == action else None
        if slot is None:
            slot = next(
                (s for s in action.slots if s.name_display == id_obj.name), None
            )
        if slot is None:
            slot = action.slots.new(id_type="OBJECT", name=id_obj.name)
        layer = action.layers.new("Layer") if not action.layers else action.layers[0]
        strip = layer.strips.new(type="KEYFRAME") if not layer.strips else layer.strips[0]
        return strip.channelbag(slot, ensure=True), slot
    return action, None  # pre-4.4: Action itself owns .fcurves


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


def import_sequences(shape: Shape, arm_obj: bpy.types.Object, bone_name_by_node: dict[int, str], bmats=()) -> list[bpy.types.Action]:
    rest_local = _rest_local_matrices(shape)
    decal_names = decal_names_by_index(shape)
    actions = []
    for seq in shape.sequences:
        name = shape.name(seq.name_index) or "sequence"
        action = bpy.data.actions.new(name)
        # a sequence only ever plays from an NLA strip, and deleting that track
        # must not take the action with it
        action.use_fake_user = True
        _store_sequence_props(action, seq, shape, bmats)
        bag, _slot = _action_channelbag(action, arm_obj)

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
            # Only the channels the sequence actually animates.  A sequence can
            # rotate a node without translating it -- woodDoor01's `open` does
            # exactly that for two of its four nodes -- and since export infers
            # the matters sets from the curves that exist, writing both here
            # would tell it every node is translated too.  A node left without
            # a location curve stays at its rest translation, which is what the
            # zero basis would have produced anyway.
            #
            # Blend sequences are the exception: their poses are raw offsets
            # rather than deltas from rest, so a missing channel is not the
            # same as an identity one and both are written.
            base = f'pose.bones["{bone}"]'
            if blend or node in rot_members:
                _write_fcurves(
                    bag, f"{base}.rotation_quaternion", 4, [[q.w, q.x, q.y, q.z] for q in quats]
                )
            if blend or node in trans_members:
                _write_fcurves(bag, f"{base}.location", 3, [list(v) for v in locs])

        # object states and decal states ride in the same slot as the bones, so
        # one strip drives pose, visibility and damage together.  These curves
        # are what export reads back -- there is no stored copy beside them.
        _write_scale_fcurves(bag, shape, seq, bone_name_by_node, n)
        write_tracks(bag, arm_obj, _object_state_tracks(shape, seq))
        write_decal_fcurves(bag, action, arm_obj, _decal_tracks(shape, seq), decal_names)

        for i, trig in enumerate(_seq_triggers(shape, seq)):
            state_bit = trig.state & 0x3FFFFFFF
            on = bool(trig.state & (1 << 31))
            marker = action.pose_markers.new(f"trig{state_bit.bit_length() - 1 if state_bit else 0}:{'on' if on else 'off'}")
            marker.frame = 1 + round(trig.pos * max(n - 1, 0))
        actions.append(action)
    return actions


def _write_scale_fcurves(bag, shape: Shape, seq: Sequence, bone_name_by_node, n: int) -> None:
    """Node scale animation as pose-bone scale channels.

    It used to ride as a dts_scale_anim JSON blob, so scaling a pose bone in
    Blender produced nothing and the stored arrays were the only truth.  Two
    of the three DTS forms map straight onto a bone's scale:

    - uniform: one factor, written to all three components
    - aligned: three factors along the node's own axes

    The third, arbitrary, is per-axis factors *plus* an orientation quaternion
    for the axes to be measured along, which a bone's scale cannot express.  No
    sequence in the 630-shape corpus uses it; it is dropped here with a warning
    and refused on export rather than half-represented.
    """
    if not (seq.flags & SEQ_ANY_SCALE):
        return
    count = seq.scale_matters.count()
    for node in sorted(seq.scale_matters.indices()):
        bone = bone_name_by_node.get(node)
        if bone is None:
            continue
        ordinal = seq.scale_matters.ordinal_of(node)
        samples = []
        for kf in range(n):
            i = seq.base_scale + ordinal * n + kf
            if seq.animates_uniform_scale():
                f = shape.node_uniform_scales[i] if i < len(shape.node_uniform_scales) else 1.0
                samples.append([f, f, f])
            elif seq.animates_aligned_scale():
                v = (
                    shape.node_aligned_scales[i]
                    if i < len(shape.node_aligned_scales)
                    else (1.0, 1.0, 1.0)
                )
                samples.append(list(v))
            else:
                v = (
                    shape.node_arbitrary_scale_factors[i]
                    if i < len(shape.node_arbitrary_scale_factors)
                    else (1.0, 1.0, 1.0)
                )
                samples.append(list(v))
        if samples:
            _write_fcurves(bag, f'pose.bones["{bone}"].scale', 3, samples)
    del count


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


def _decal_tracks(shape: Shape, seq: Sequence) -> dict[int, list]:
    """{decal index: [state per keyframe]} for a sequence."""
    n = seq.num_keyframes
    tracks = {}
    for di in seq.decal_matters.indices():
        track = []
        for kf in range(n):
            idx = seq.base_decal_state + seq.decal_matters.ordinal_of(di) * n + kf
            track.append(shape.decal_states[idx] if idx < len(shape.decal_states) else 0)
        tracks[di] = track
    return tracks


def _object_state_tracks(shape: Shape, seq: Sequence) -> dict[str, dict[str, list]]:
    """{object name: {vis|frame|matframe: [value per keyframe]}} for a sequence.

    One block of states per object in the *union* of the three matters sets --
    an object with both a vis and a frame track has one block carrying both,
    not one per channel.  Indexing with a per-channel ordinal reads the right
    block only while the sets happen to agree: v22_disc's Activate fades two
    objects and animates the frames of the second, so the frame track read out
    of the first object's block, which has none, and arrived as sixteen zeroes.
    """
    n = seq.num_keyframes
    membership = object_membership(seq)
    tracks: dict[str, dict[str, list]] = {}
    for obj_index in membership.indices():
        if obj_index >= len(shape.objects):
            continue
        first = seq.base_object_state + membership.ordinal_of(obj_index) * n
        if first + n > len(shape.object_states):
            continue  # a short state table; the sequence names more than it has
        states = shape.object_states[first : first + n]
        base_name = shape.name(shape.objects[obj_index].name_index)
        by_kind = tracks.setdefault(base_name, {})
        if seq.vis_matters.test(obj_index):
            by_kind["vis"] = [st.vis for st in states]
        if seq.frame_matters.test(obj_index):
            by_kind["frame"] = [st.frame_index for st in states]
        if seq.mat_frame_matters.test(obj_index):
            by_kind["matframe"] = [st.mat_frame_index for st in states]
    return tracks


def _store_sequence_props(action, seq: Sequence, shape: Shape, bmats) -> None:
    action["dts_sequence"] = True
    action["dts_flags"] = seq.flags
    action["dts_cyclic"] = bool(seq.flags & SEQ_CYCLIC)
    action["dts_blend"] = bool(seq.flags & SEQ_BLEND)
    action["dts_makepath"] = bool(seq.flags & SEQ_MAKE_PATH)
    action["dts_priority"] = seq.priority
    action["dts_duration"] = seq.duration
    action["dts_tool_begin"] = seq.tool_begin

    props = action.dts_sequence_props
    props.schema_version = SCHEMA_VERSION
    props.ground.clear()
    for i in range(seq.num_ground_frames):
        idx = seq.first_ground_frame + i
        if idx >= len(shape.ground_translations):
            # v22/23 shapes can claim ground frames they don't carry ("shapes
            # accidentally had no ground transforms"); the engine guards this
            # in TSThread::getGround, so do we
            break
        q = shape.ground_rotations[idx]
        item = props.ground.add()
        item.translation = tuple(shape.ground_translations[idx])
        item.rotation = (q.x, q.y, q.z, q.w)

    props.triggers.clear()
    for trigger in _seq_triggers(shape, seq):
        fields = parse_trigger_state(trigger.state)
        item = props.triggers.add()
        item.state = fields["state"]
        item.on = fields["on"]
        item.invert_on_reverse = fields["invert_on_reverse"]
        item.pos = trigger.pos

    # a bit per entry in the shape's IFL table; the entry names a material
    # slot, and that material is what the sequence actually advances
    props.ifl_matters.clear()
    for index in sorted(seq.ifl_matters.indices()):
        if index >= len(shape.ifl_materials):
            continue
        slot = shape.ifl_materials[index].raw[1]
        if 0 <= slot < len(bmats):
            props.ifl_matters.add().material = bmats[slot]

    if seq.flags & SEQ_ANY_SCALE:
        # only the mode; the factors themselves become pose-bone scale curves
        props.scale_mode = (
            "UNIFORM" if seq.animates_uniform_scale()
            else "ALIGNED" if seq.animates_aligned_scale()
            else "ARBITRARY"
        )


# ----------------------------------------------------------------------
# export: Actions -> Shape sequences
# ----------------------------------------------------------------------


def export_sequences(
    shape: Shape,
    arm_obj: bpy.types.Object,
    actions: list[bpy.types.Action],
    node_index_by_bone: dict[str, int],
    object_index_by_name: dict[str, int],
    decal_index_map: dict[int, int] | None = None,
    baked_decal_objects: dict[int, int] | None = None,
) -> list[str]:
    """Append sequences built from actions to shape.  Returns warnings."""
    # the IFL table is derived and already built by the time this runs, so a
    # material pointer resolves through its entry name -- which is the
    # material's own name plus .ifl, exact for every entry in the corpus
    ifl_index_of = {
        material_name_for(shape.name(entry.raw[0])): i
        for i, entry in enumerate(shape.ifl_materials)
    }
    warnings = []
    decal_index_map = decal_index_map or {}
    baked_decal_objects = baked_decal_objects or {}
    rest_local = _rest_local_matrices(shape)

    for action in actions:
        # The keys in the action are the length.  This used to prefer a stored
        # dts_keyframes, which meant adding or removing a keyframe in Blender
        # changed nothing: an imported sequence always had the property.
        n = _keyframe_count(action)
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
        # Which nodes a sequence animates is read off the channels that exist,
        # not from a stored copy.  The importer writes a curve for every member
        # of both sets, so inference reproduces them exactly -- and once it is
        # the only path, adding a bone channel in Blender marks that node
        # instead of being ignored.
        rot_set, trans_set = TSIntegerSet(), TSIntegerSet()
        for bone, props in bone_channels.items():
            node = node_index_by_bone[bone]
            has_rot = any(p.startswith("rotation_") for p in props)
            has_loc = "location" in props
            if has_rot:
                rot_set.set(node)
            if has_loc:
                trans_set.set(node)
            if not has_rot and not has_loc:
                # a scale-only channel still needs the node in the tables the
                # engine indexes by ordinal
                rot_set.set(node)
                trans_set.set(node)
        seq.rotation_matters = rot_set
        seq.translation_matters = trans_set

        seq.base_rotation = len(shape.node_rotations)
        seq.base_translation = len(shape.node_translations)
        bone_by_node = {node_index_by_bone[b]: b for b in bone_channels}
        # channel-major, and only over the nodes each set actually marks --
        # the reader indexes these arrays by ordinal within the matters set, so
        # writing a row for an unmarked node shifts every later node's track
        for node in sorted(rot_set.indices()):
            chans = bone_channels.get(bone_by_node.get(node, ""), {})
            for kf in range(n):
                basis = _sample_basis(chans, kf + 1)
                local = basis if blend else rest_local[node] @ basis
                shape.node_rotations.append(blender_quat_to_dts(local.to_quaternion()))
        for node in sorted(trans_set.indices()):
            chans = bone_channels.get(bone_by_node.get(node, ""), {})
            for kf in range(n):
                basis = _sample_basis(chans, kf + 1)
                local = basis if blend else rest_local[node] @ basis
                shape.node_translations.append(tuple(local.to_translation()))

        # ground frames
        ground = [
            (list(item.translation), list(item.rotation))
            for item in action.dts_sequence_props.ground
        ]
        seq.first_ground_frame = len(shape.ground_translations)
        seq.num_ground_frames = len(ground)
        for t, q in ground:
            shape.ground_translations.append(tuple(t))
            shape.ground_rotations.append(Quat16(*q))

        # triggers
        trigs = [
            (pack_trigger(item.state, item.on, item.invert_on_reverse), item.pos)
            for item in action.dts_sequence_props.triggers
        ]
        seq.first_trigger = len(shape.triggers)
        seq.num_triggers = len(trigs)
        for state, pos in trigs:
            shape.triggers.append(Trigger(int(state) & 0xFFFFFFFF, float(pos)))

        # object-state tracks, sampled from the curves the user edits
        obj_anim = read_tracks(action, n)
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

        # a baked decal is an object, so its state track is a visibility track.
        # A decal state is -1 for off and a frame index for on, and the baked
        # form has one frame, so the whole range of "on" collapses to visible.
        for key, track in read_decal_tracks(action, n).items():
            oi = baked_decal_objects.get(int(key))
            if oi is None:
                if baked_decal_objects:
                    warnings.append(
                        f"action {action.name!r}: decal {key} was not baked as a "
                        f"mesh (it covers no faces); its state track was dropped"
                    )
                continue
            vis = [1.0 if float(x) >= 0.0 else 0.0 for x in track[:n]]
            vis += [0.0] * max(0, n - len(vis))
            tracked.append((oi, {"vis": vis}))
            vis_set.set(oi)

        seq.vis_matters, seq.frame_matters, seq.mat_frame_matters = vis_set, frame_set, matframe_set
        seq.base_object_state = len(shape.object_states)
        for oi, tracks in sorted(tracked, key=lambda pair: pair[0]):
            vis = tracks.get("vis", [1.0] * n)
            frame = tracks.get("frame", [0] * n)
            matframe = tracks.get("matframe", [0] * n)
            for kf in range(n):
                shape.object_states.append(
                    ObjectState(float(vis[kf]), int(frame[kf]), int(matframe[kf]))
                )

        # ifl membership.  The file stores a bit per entry in the shape's IFL
        # table, and that table is derived from the materials in list order, so
        # a material pointer resolves to its position in it.
        iset = TSIntegerSet()
        for item in action.dts_sequence_props.ifl_matters:
            # the entry name is bare, so compare bare: a material called
            # skins\flare has an entry called flare.ifl
            dts_name = (
                str(item.material.get("dts_name") or item.material.name)
                .replace("\\", "/").rpartition("/")[2]
                if item.material else None
            )
            index = ifl_index_of.get(dts_name) if dts_name else None
            if index is None:
                warnings.append(
                    f"action {action.name!r}: IFL entry "
                    f"{item.material.name if item.material else '<empty>'!r} is not an "
                    f"IFL material of this shape; the sequence will not advance it"
                )
                continue
            iset.set(index)
        seq.ifl_matters = iset

        # decal-state tracks, likewise.  Empty when the decals were baked as
        # meshes: there is no decal table to index, and the tracks already left
        # as visibility on the baked objects above.
        decal_anim = {} if baked_decal_objects else read_decal_tracks(action, n)
        dset = TSIntegerSet()
        decal_tracked = []
        for k, track in decal_anim.items():
            new = decal_index_map.get(int(k))
            if new is None:
                warnings.append(
                    f"action {action.name!r}: decal {k} no longer exists; state track dropped"
                )
                continue
            dset.set(new)
            decal_tracked.append((new, track))
        seq.decal_matters = dset
        seq.base_decal_state = len(shape.decal_states)
        for _, track in sorted(decal_tracked):
            padded = list(track[:n]) + [0] * max(0, n - len(track))
            shape.decal_states.extend(int(x) for x in padded)

        # scale animation, read off the pose-bone scale channels
        scale_mode = action.dts_sequence_props.scale_mode
        scaled = {
            node_index_by_bone[bone]: props["scale"]
            for bone, props in bone_channels.items()
            if "scale" in props and bone in node_index_by_bone
        }
        if scaled and scale_mode == "ARBITRARY":
            from .blender_to_shape import ExportError

            raise ExportError(
                f"sequence {action.name!r} animates arbitrary node scale, which is per-axis "
                f"factors plus an orientation a bone's scale cannot express — set "
                f"dts_scale_mode to UNIFORM or ALIGNED on the action, or remove the "
                f"scale channels"
            )
        if scaled:
            scale_set = TSIntegerSet()
            for node in scaled:
                scale_set.set(node)
            seq.scale_matters = scale_set
            uniform = scale_mode != "ALIGNED"
            seq.flags |= SEQ_UNIFORM_SCALE if uniform else SEQ_ALIGNED_SCALE
            seq.base_scale = (
                len(shape.node_uniform_scales) if uniform else len(shape.node_aligned_scales)
            )
            for node in sorted(scaled):
                chans = scaled[node]
                for kf in range(n):
                    axes = [
                        chans[i].evaluate(kf + 1) if i in chans else 1.0
                        for i in range(3)
                    ]
                    if uniform:
                        shape.node_uniform_scales.append(axes[0])
                    else:
                        shape.node_aligned_scales.append(tuple(axes))

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

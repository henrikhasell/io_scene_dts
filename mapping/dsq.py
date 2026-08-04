"""DSQ files <-> Blender actions on an existing armature.

Import matches DSQ nodes to bones by (case-insensitive) name, with duplicate
names matched by ordinal occurrence — the engine's importSequences nodeMap.
Export writes every bone as a DSQ node so the file applies to any shape with
matching node names.
"""

from __future__ import annotations

import json

import bpy

from ..dtslib import DsqFile, Quat16, Sequence, Trigger, TSIntegerSet
from ..dtslib.types import SEQ_BLEND, SEQ_CYCLIC, SEQ_MAKE_PATH
from .naming import strip_blender_dedup
from .sequences import (
    _action_channelbag,
    _iter_fcurves,
    _keyframe_count,
    _sample_basis,
    _write_fcurves,
    blender_quat_to_dts,
    dts_local_matrix,
)


def bone_rest_locals(arm_obj) -> dict[str, object]:
    out = {}
    for b in arm_obj.data.bones:
        m = (b.parent.matrix_local.inverted() @ b.matrix_local) if b.parent else b.matrix_local.copy()
        out[b.name] = m
    return out


def _dts_bone_name(bone) -> str:
    return bone.get("dts_name") or strip_blender_dedup(bone.name)


def _map_dsq_nodes(dsq: DsqFile, arm_obj) -> tuple[dict[int, str], list[str]]:
    """DSQ node index -> bone name; duplicate names matched by occurrence."""
    warnings = []
    bones = list(arm_obj.data.bones)
    by_name: dict[str, list[str]] = {}
    for b in bones:
        by_name.setdefault(_dts_bone_name(b).lower(), []).append(b.name)
    seen: dict[str, int] = {}
    mapping = {}
    for i, name in enumerate(dsq.node_names):
        low = name.lower()
        count = seen.get(low, 0)
        seen[low] = count + 1
        candidates = by_name.get(low, [])
        if count < len(candidates):
            mapping[i] = candidates[count]
        else:
            warnings.append(f"DSQ node {name!r} not found in armature; its channels are dropped")
    return mapping, warnings


def dsq_to_actions(dsq: DsqFile, arm_obj) -> tuple[list[bpy.types.Action], list[str]]:
    mapping, warnings = _map_dsq_nodes(dsq, arm_obj)
    rest = bone_rest_locals(arm_obj)
    actions = []

    for name, seq in zip(dsq.sequence_names, dsq.sequences):
        action = bpy.data.actions.new(name or "sequence")
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
        blend = bool(seq.flags & SEQ_BLEND)
        bag, slot = _action_channelbag(action, arm_obj)

        rot_members = sorted(seq.rotation_matters.indices())
        trans_members = sorted(seq.translation_matters.indices())
        for node in sorted(set(rot_members) | set(trans_members)):
            bone = mapping.get(node)
            if bone is None:
                continue
            quats, locs = [], []
            for kf in range(n):
                if node in rot_members:
                    q16 = dsq.node_rotations[seq.base_rotation + seq.rotation_matters.ordinal_of(node) * n + kf]
                else:
                    q16 = None
                if node in trans_members:
                    t = dsq.node_translations[seq.base_translation + seq.translation_matters.ordinal_of(node) * n + kf]
                else:
                    t = None
                rest_m = rest[bone]
                if q16 is None and t is None:
                    local = rest_m
                else:
                    q = q16 if q16 is not None else Quat16.identity()
                    local = dts_local_matrix(q, t if t is not None else (0.0, 0.0, 0.0))
                    if q16 is None:
                        local = rest_m @ local  # translation-only: unlikely, keep sane
                basis = local if blend else rest_m.inverted() @ local
                quats.append(basis.to_quaternion())
                locs.append(basis.to_translation())
            for i in range(1, n):
                if quats[i].dot(quats[i - 1]) < 0:
                    quats[i].negate()
            base = f'pose.bones["{bone}"]'
            _write_fcurves(bag, f"{base}.rotation_quaternion", 4, [[q.w, q.x, q.y, q.z] for q in quats])
            _write_fcurves(bag, f"{base}.location", 3, [list(v) for v in locs])

        ground = []
        for i in range(seq.num_ground_frames):
            t = dsq.ground_translations[seq.first_ground_frame + i]
            q = dsq.ground_rotations[seq.first_ground_frame + i]
            ground.append([list(t), [q.x, q.y, q.z, q.w]])
        if ground:
            action["dts_ground"] = json.dumps(ground)

        trigs = [
            [t.state, t.pos]
            for t in dsq.triggers[seq.first_trigger : seq.first_trigger + seq.num_triggers]
        ]
        if trigs:
            action["dts_triggers"] = json.dumps(trigs)

        actions.append(action)
    return actions, warnings


def actions_to_dsq(arm_obj, actions: list[bpy.types.Action], version: int = 24) -> tuple[DsqFile, list[str]]:
    warnings = []
    dsq = DsqFile(version=version)

    bones = list(arm_obj.data.bones)
    if bones and all("dts_node_index" in b for b in bones):
        bones.sort(key=lambda b: int(b["dts_node_index"]))
    node_index_by_bone = {b.name: i for i, b in enumerate(bones)}
    dsq.node_names = [_dts_bone_name(b) for b in bones]
    rest = bone_rest_locals(arm_obj)

    for action in actions:
        n = int(action.get("dts_keyframes", 0)) or _keyframe_count(action)
        if n <= 0:
            warnings.append(f"action {action.name!r} has no keyframes; skipped")
            continue
        seq = Sequence()
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

        bone_channels: dict[str, dict[str, dict[int, object]]] = {}
        for fc in _iter_fcurves(action):
            if not fc.data_path.startswith('pose.bones["'):
                continue
            bone = fc.data_path.split('"')[1]
            if bone not in node_index_by_bone:
                warnings.append(f"action {action.name!r}: bone {bone!r} unknown; channel dropped")
                continue
            prop = fc.data_path.rsplit(".", 1)[-1]
            bone_channels.setdefault(bone, {}).setdefault(prop, {})[fc.array_index] = fc

        members = sorted(node_index_by_bone[b] for b in bone_channels)
        rot_set, trans_set = TSIntegerSet(), TSIntegerSet()
        for node in members:
            rot_set.set(node)
            trans_set.set(node)
        seq.rotation_matters = rot_set
        seq.translation_matters = trans_set

        bone_by_node = {node_index_by_bone[b]: b for b in bone_channels}
        seq.base_rotation = len(dsq.node_rotations)
        seq.base_translation = len(dsq.node_translations)
        for node in members:
            bone = bone_by_node[node]
            for kf in range(n):
                basis = _sample_basis(bone_channels[bone], kf + 1)
                local = basis if blend else rest[bone] @ basis
                dsq.node_rotations.append(blender_quat_to_dts(local.to_quaternion()))
        for node in members:
            bone = bone_by_node[node]
            for kf in range(n):
                basis = _sample_basis(bone_channels[bone], kf + 1)
                local = basis if blend else rest[bone] @ basis
                dsq.node_translations.append(tuple(local.to_translation()))

        ground = json.loads(action.get("dts_ground", "[]") or "[]")
        seq.first_ground_frame = len(dsq.ground_translations)
        seq.num_ground_frames = len(ground)
        for t, q in ground:
            dsq.ground_translations.append(tuple(t))
            dsq.ground_rotations.append(Quat16(*q))

        trigs = json.loads(action.get("dts_triggers", "[]") or "[]")
        seq.first_trigger = len(dsq.triggers)
        seq.num_triggers = len(trigs)
        for state, pos in trigs:
            dsq.triggers.append(Trigger(int(state) & 0xFFFFFFFF, float(pos)))

        dsq.sequences.append(seq)
        dsq.sequence_names.append(strip_blender_dedup(action.name))
    return dsq, warnings

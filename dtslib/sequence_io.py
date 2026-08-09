"""Sequence record and DSQ file IO.

Ports of TSShape::Sequence::read/write (tsShapeOldRead.cc:1350/:1463) with all
version branches, TSIntegerSet::read/write (tsIntegerSet.cc), and
TSShape::exportSequences/importSequences (:945/:1050 — the DSQ file format).
"""

from __future__ import annotations

import struct

from .errors import DtsError, DtsUnsupportedVersion
from .primitives import MAX_TS_SET_DWORDS, Quat16, TSIntegerSet
from .stream import StreamReader, StreamWriter
from .types import (
    SEQ_BLEND,
    SEQ_CYCLIC,
    SEQ_MAKE_PATH,
    DsqFile,
    Sequence,
    Trigger,
)

DSQ_MIN_VERSION = 17
DSQ_MAX_VERSION = 24


# ----------------------------------------------------------------------
# TSIntegerSet
# ----------------------------------------------------------------------


def read_integer_set(r: StreamReader) -> TSIntegerSet:
    legacy = r.s32()  # "don't care" word, preserved for byte-identity
    sz = r.s32()
    if sz > MAX_TS_SET_DWORDS:
        raise DtsError(f"TSIntegerSet too large: {sz} dwords (max {MAX_TS_SET_DWORDS})")
    mask = 0
    for i in range(sz):
        mask |= r.u32() << (32 * i)
    return TSIntegerSet(mask, legacy=legacy, stored_dwords=sz)


def write_integer_set(w: StreamWriter, s: TSIntegerSet) -> None:
    w.s32(s.legacy)
    sz = s.stored_dwords if s.stored_dwords is not None else s.trimmed_dwords()
    w.s32(sz)
    for word in s.words(sz):
        w.u32(word)


# ----------------------------------------------------------------------
# Sequence record
# ----------------------------------------------------------------------


def read_sequence(
    r: StreamReader, version: int, read_name_index: bool, kf_starts: list | None = None
) -> Sequence:
    """Sequence::read.  version is the enclosing file's version.

    Pre-v17 files track a *range* of keyframes rather than a count, and the
    sequence's base state indices live in the (obsolete) keyframe table instead
    of in the sequence record.  ``kf_starts`` collects each sequence's start
    keyframe for the caller's fixup pass — the engine's ``kfStart`` vector
    (tsShapeOldRead.cc:374).
    """
    seq = Sequence()
    if read_name_index:
        seq.name_index = r.s32()
    if version > 21:
        seq.flags = r.u32()
    if version < 17:
        start_keyframe = r.s32()
        end_keyframe = r.s32()
        seq.num_keyframes = end_keyframe - start_keyframe
        if kf_starts is None:
            raise DtsError(f"v{version} sequence needs the keyframe table to be readable")
        kf_starts.append(start_keyframe)
    else:
        seq.num_keyframes = r.s32()
    seq.duration = r.f32()

    if version < 22:
        # three separate bools instead of the flags word
        if r.bool8():
            seq.flags |= SEQ_BLEND
        if r.bool8():
            seq.flags |= SEQ_CYCLIC
        if r.bool8():
            seq.flags |= SEQ_MAKE_PATH

    seq.priority = r.s32()
    seq.first_ground_frame = r.s32()
    seq.num_ground_frames = r.s32()
    if version > 21:
        seq.base_rotation = r.s32()
        seq.base_translation = r.s32()
        seq.base_scale = r.s32()
        seq.base_object_state = r.s32()
        seq.base_decal_state = r.s32()
    elif version >= 17:
        seq.base_rotation = r.s32()
        seq.base_translation = seq.base_rotation
        seq.base_object_state = r.s32()
        seq.base_decal_state = r.s32()
    # pre-17: no base indices in the record at all — the keyframe table has them
    seq.first_trigger = r.s32()
    seq.num_triggers = r.s32()
    seq.tool_begin = r.f32()

    seq.rotation_matters = read_integer_set(r)
    if version < 22:
        seq.translation_matters = seq.rotation_matters.copy()
    else:
        seq.translation_matters = read_integer_set(r)
        seq.scale_matters = read_integer_set(r)
    if version < 17:
        read_integer_set(r)  # obsolete objectMembership, recomputed from the three below
    seq.decal_matters = read_integer_set(r)
    seq.ifl_matters = read_integer_set(r)
    seq.vis_matters = read_integer_set(r)
    seq.frame_matters = read_integer_set(r)
    seq.mat_frame_matters = read_integer_set(r)
    if version < 17:
        read_integer_set(r)  # obsolete nodeTransformStatic
    return seq


def object_membership(seq: Sequence) -> TSIntegerSet:
    """The pre-v17 objectMembership set: every object the sequence touches.

    The engine reads the stored set and throws it away, recomputing it as this
    union (rearrangeKeyframeData, tsShapeOldRead.cc:722) — so this is what to
    write, and a stored set that disagreed never mattered.
    """
    return TSIntegerSet(
        seq.frame_matters.mask | seq.mat_frame_matters.mask | seq.vis_matters.mask
    )


def write_sequence(
    w: StreamWriter,
    seq: Sequence,
    write_name_index: bool,
    version: int = 24,
    start_keyframe: int = 0,
) -> None:
    """Sequence::write, in ``version``'s layout.

    The engine only ever writes the current version, so the reference for
    anything older is Sequence::read run backwards (tsShapeOldRead.cc:1350).
    Fields the older record has nowhere for — the base indices pre-v17, the
    scale bases and the flags word pre-v22 — are the caller's problem: pass a
    sequence whose ground/base indices are already in ``version``'s address
    space (``dataclasses.replace``), and clear scale animation first.
    """
    if write_name_index:
        w.s32(seq.name_index)
    if version > 21:
        w.u32(seq.flags)
    if version < 17:
        # a keyframe *range*, and the state bases come from the keyframe table
        w.s32(start_keyframe)
        w.s32(start_keyframe + seq.num_keyframes)
    else:
        w.s32(seq.num_keyframes)
    w.f32(seq.duration)
    if version < 22:
        w.u8(1 if seq.flags & SEQ_BLEND else 0)
        w.u8(1 if seq.flags & SEQ_CYCLIC else 0)
        w.u8(1 if seq.flags & SEQ_MAKE_PATH else 0)
    w.s32(seq.priority)
    w.s32(seq.first_ground_frame)
    w.s32(seq.num_ground_frames)
    if version > 21:
        w.s32(seq.base_rotation)
        w.s32(seq.base_translation)
        w.s32(seq.base_scale)
        w.s32(seq.base_object_state)
        w.s32(seq.base_decal_state)
    elif version >= 17:
        # one node track per node: rotation and translation share a base
        w.s32(seq.base_rotation)
        w.s32(seq.base_object_state)
        w.s32(seq.base_decal_state)
    w.s32(seq.first_trigger)
    w.s32(seq.num_triggers)
    w.f32(seq.tool_begin)

    write_integer_set(w, seq.rotation_matters)
    if version > 21:
        write_integer_set(w, seq.translation_matters)
        write_integer_set(w, seq.scale_matters)
    if version < 17:
        write_integer_set(w, object_membership(seq))
    write_integer_set(w, seq.decal_matters)
    write_integer_set(w, seq.ifl_matters)
    write_integer_set(w, seq.vis_matters)
    write_integer_set(w, seq.frame_matters)
    write_integer_set(w, seq.mat_frame_matters)
    if version < 17:
        write_integer_set(w, TSIntegerSet())  # obsolete nodeTransformStatic


# ----------------------------------------------------------------------
# DSQ files (TSShape::exportSequences / importSequences)
# ----------------------------------------------------------------------


def _read_quat16(r: StreamReader) -> Quat16:
    return Quat16(r.s16(), r.s16(), r.s16(), r.s16())


def _write_quat16(w: StreamWriter, q: Quat16) -> None:
    w.s16(q.x)
    w.s16(q.y)
    w.s16(q.z)
    w.s16(q.w)


def _read_point3(r: StreamReader) -> tuple[float, float, float]:
    return (r.f32(), r.f32(), r.f32())


def _write_point3(w: StreamWriter, p) -> None:
    w.f32(p[0])
    w.f32(p[1])
    w.f32(p[2])


def read_dsq(data: bytes) -> DsqFile:
    try:
        return _read_dsq(data)
    except struct.error as e:
        raise DtsError(f"truncated or corrupt DSQ file: {e}") from e


def _read_dsq(data: bytes) -> DsqFile:
    if len(data) < 4:
        raise DtsError(f"file too short ({len(data)} bytes)")
    r = StreamReader(data)
    dsq = DsqFile()
    dsq.version = r.s32()
    if not (DSQ_MIN_VERSION <= dsq.version <= DSQ_MAX_VERSION):
        raise DtsUnsupportedVersion(dsq.version, "DSQ")

    num_nodes = r.s32()
    dsq.node_names = [r.s32_string() for _ in range(num_nodes)]

    legacy_objects = r.s32()  # always 0: "don't pretend to support object export"
    if legacy_objects != 0:
        raise DtsError(f"DSQ legacy object count is {legacy_objects}, expected 0")
    dsq.num_source_objects = r.s32()

    if dsq.version > 21:
        dsq.node_rotations = [_read_quat16(r) for _ in range(r.s32())]
        dsq.node_translations = [_read_point3(r) for _ in range(r.s32())]
        dsq.node_uniform_scales = [r.f32() for _ in range(r.s32())]
        dsq.node_aligned_scales = [_read_point3(r) for _ in range(r.s32())]
        n_arb = r.s32()
        dsq.node_arbitrary_scale_rots = [_read_quat16(r) for _ in range(n_arb)]
        # NOTE: no separate count word — factors reuse the rots count
        dsq.node_arbitrary_scale_factors = [_read_point3(r) for _ in range(n_arb)]
        n_ground = r.s32()
        dsq.ground_translations = [_read_point3(r) for _ in range(n_ground)]
        # NOTE: rotations also reuse the preceding count
        dsq.ground_rotations = [_read_quat16(r) for _ in range(n_ground)]
    else:
        # pre-22: node rotations and translations interleaved under one count
        n = r.s32()
        for _ in range(n):
            dsq.node_rotations.append(_read_quat16(r))
            dsq.node_translations.append(_read_point3(r))

    legacy_states = r.s32()  # object state count, always 0 on export
    if legacy_states != 0:
        raise DtsError(f"DSQ legacy object-state count is {legacy_states}, expected 0")

    num_sequences = r.s32()
    for _ in range(num_sequences):
        name = r.s32_string()
        seq = read_sequence(r, dsq.version, read_name_index=False)
        dsq.sequence_names.append(name)
        dsq.sequences.append(seq)

    if dsq.version > 8:
        num_triggers = r.s32()
        for _ in range(num_triggers):
            dsq.triggers.append(Trigger(r.u32(), r.f32()))

    # pre-22 DSQs keep ground frames inside the node arrays; migrate them out
    # (importSequences, tsShapeOldRead.cc:1309)
    if dsq.version < 22:
        for seq in dsq.sequences:
            first = seq.first_ground_frame
            n = seq.num_ground_frames
            old = len(dsq.ground_translations)
            dsq.ground_translations.extend(dsq.node_translations[first : first + n])
            dsq.ground_rotations.extend(dsq.node_rotations[first : first + n])
            seq.first_ground_frame = old
    return dsq


def write_dsq(dsq: DsqFile, version: int = 24) -> bytes:
    """Write a DSQ in the modern (v22+) layout.  version stamps the header."""
    if version < 22:
        raise DtsError("write_dsq only writes the modern layout (version >= 22)")
    w = StreamWriter()
    w.s32(version)

    w.s32(len(dsq.node_names))
    for name in dsq.node_names:
        w.s32_string(name)

    w.s32(0)  # legacy exported-object count
    w.s32(dsq.num_source_objects)

    w.s32(len(dsq.node_rotations))
    for q in dsq.node_rotations:
        _write_quat16(w, q)
    w.s32(len(dsq.node_translations))
    for p in dsq.node_translations:
        _write_point3(w, p)
    w.s32(len(dsq.node_uniform_scales))
    for f in dsq.node_uniform_scales:
        w.f32(f)
    w.s32(len(dsq.node_aligned_scales))
    for p in dsq.node_aligned_scales:
        _write_point3(w, p)
    w.s32(len(dsq.node_arbitrary_scale_rots))
    for q in dsq.node_arbitrary_scale_rots:
        _write_quat16(w, q)
    for p in dsq.node_arbitrary_scale_factors:  # no separate count word
        _write_point3(w, p)
    w.s32(len(dsq.ground_translations))
    for p in dsq.ground_translations:
        _write_point3(w, p)
    for q in dsq.ground_rotations:  # no separate count word
        _write_quat16(w, q)

    w.s32(0)  # legacy object-state count

    w.s32(len(dsq.sequences))
    for name, seq in zip(dsq.sequence_names, dsq.sequences):
        w.s32_string(name)
        write_sequence(w, seq, write_name_index=False)

    w.s32(len(dsq.triggers))
    for t in dsq.triggers:
        w.u32(t.state)
        w.f32(t.pos)
    return w.getvalue()

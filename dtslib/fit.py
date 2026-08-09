"""What each DTS version can hold, and how to make a shape fit one.

``write_shape`` refuses a shape the requested version has nowhere to put --
losing an animation's ground speed or its scale track silently is worse than
not writing the file.  ``fit_to_version`` is the caller saying "lose it, but
tell me": it mutates the shape into something the version *can* hold and
returns one message per thing that cost data.

Not everything it does costs data.  Older versions store less because the
engine recomputes more on load -- mesh bounds, poly counts, the runtime
sibling links -- and one stores its animation tables in a different order.
Those parts are rewritten rather than reported, and the point of doing them
here is that the fitted shape then equals what reading the written file gives
back: nothing about a round trip is left to be "close enough".

Version by version, going down:

- **24** holds everything this library models.
- **23** and **22** have no ground-frame storage at all (the engine calls that
  an accident, tsShape.cc:786) -- ground frames are dropped.
- **21 and older** store one node track per node, rotation and translation
  together, and keep ground frames at the end of that same array.  Scale
  animation does not exist.
- **18 and older** are the flat-stream format: no mesh bounds, no vertex
  sharing between detail levels, no merge indices, no LOD error metrics, and
  decals with no texture-generation planes.
- **16 and older** index their meshes through a separate list, and **15**
  stores animation keyframe-major.  Neither costs anything -- see
  ``old_writer``.
"""

from __future__ import annotations

import dataclasses

from .errors import DtsWriteError
from .mesh_io import compute_mesh_bounds
from .primitives import TSIntegerSet
from .types import (
    DECAL_MESH,
    MAT_NEVER_ENV_MAP,
    MAT_TRANSLUCENT,
    SEQ_ANY_SCALE,
    Shape,
)

MIN_VERSION = 15
MAX_VERSION = 24

# the two versions with no ground-frame storage anywhere (tsShape.cc:786)
NO_GROUND_STORAGE = (22, 23)


def check_version(version: int) -> None:
    if not MIN_VERSION <= version <= MAX_VERSION:
        raise DtsWriteError(
            f"cannot write DTS version {version}: this library writes "
            f"{MIN_VERSION}-{MAX_VERSION}"
        )


# ----------------------------------------------------------------------
# ground frames
# ----------------------------------------------------------------------


def strip_ground_frames(shape: Shape) -> None:
    """Explicitly remove ground-frame data so the shape can be written as v22/23.

    Clears the ground arrays and zeroes every sequence's ground fields.
    """
    shape.ground_translations = []
    shape.ground_rotations = []
    for seq in shape.sequences:
        seq.first_ground_frame = 0
        seq.num_ground_frames = 0


def _repack_ground_frames(shape: Shape) -> None:
    """Order the ground arrays by sequence, the way a read leaves them.

    The pre-v22 migration walks the sequences and appends each one's frames to
    the ground arrays in turn (tsShape.cc:768), so that order is the only one a
    written file can come back as.  Frames no sequence claims are dropped --
    reading would not find them either.
    """
    trans, rots = [], []
    for seq in shape.sequences:
        first, n = seq.first_ground_frame, seq.num_ground_frames
        seq.first_ground_frame = len(trans)
        trans.extend(shape.ground_translations[first : first + n])
        rots.extend(shape.ground_rotations[first : first + n])
        seq.num_ground_frames = len(trans) - seq.first_ground_frame
    shape.ground_translations = trans
    shape.ground_rotations = rots


# ----------------------------------------------------------------------
# node animation tracks
# ----------------------------------------------------------------------


def reachable_members(matters: TSIntegerSet, limit: int) -> list[int]:
    """The set members the engine actually animates.

    Its walk over a matters set stops at the end of the table the set indexes
    (``end = b`` in animateNodes, tsAnimate.cc:124, and animateVisibility,
    :641), so a bit at or past that end contributes no channel and no stored
    track -- three of the corpus's shapes have one.  Such a bit is always above
    every real one, so ignoring it leaves the other channels' positions alone.
    """
    return [i for i in matters.indices() if i < limit]


def trim_matters_to_tables(shape: Shape) -> None:
    """Clear matters bits naming a node, object or decal the shape lacks.

    The engine ignores them, so this changes no animation -- but the older
    layouts derive their channel counts from these sets, and a count that
    disagrees with the stored track data would scramble the transpose.
    """
    num_objects = len(shape.objects)
    for seq in shape.sequences:
        for attr, limit in (
            ("rotation_matters", len(shape.nodes)),
            ("translation_matters", len(shape.nodes)),
            ("scale_matters", len(shape.nodes)),
            ("vis_matters", num_objects),
            ("frame_matters", num_objects),
            ("mat_frame_matters", num_objects),
            ("decal_matters", len(shape.decals)),
        ):
            members = getattr(seq, attr)
            members.mask &= (1 << limit) - 1


def _with_mask(members: TSIntegerSet, mask: int) -> TSIntegerSet:
    """``members`` with a different mask, keeping its file identity.

    A TSIntegerSet carries a leading word the engine ignores and the dword count
    it was stored with; both are reproduced verbatim on write, so replacing a set
    wholesale would change bytes that mean nothing.  The count still has to grow
    if the new mask needs more words than the old one did.
    """
    out = members.copy()
    out.mask = mask
    if out.stored_dwords is not None and out.stored_dwords < out.trimmed_dwords():
        out.stored_dwords = out.trimmed_dwords()
    return out


def _channel_tracks(arr: list, base: int, members: list[int], num_keyframes: int) -> dict:
    """Split one sequence's block out of a node array, per animated node."""
    out = {}
    for ordinal, node in enumerate(members):
        start = base + ordinal * num_keyframes
        out[node] = list(arr[start : start + num_keyframes])
    return out


def node_tracks_paired(shape: Shape) -> bool:
    """True when rotation and translation animate exactly the same nodes.

    Pre-v22 a node state is one rotation *and* one translation, indexed by one
    base and one membership set, so this has to hold before such a file can be
    written.
    """
    if len(shape.node_rotations) != len(shape.node_translations):
        return False
    return all(
        seq.rotation_matters == seq.translation_matters
        and seq.base_rotation == seq.base_translation
        for seq in shape.sequences
    )


def pair_node_tracks(shape: Shape) -> None:
    """Rebuild the node arrays so every sequence animates rotation and
    translation for the same nodes, with ground frames at the tail.

    A node that only had one of the two gets a constant track at its rest
    value for the other, which is what the engine would have used anyway, so
    the animation is unchanged -- the file just carries the redundancy the
    older layout demands.  Idempotent: re-running finds the same blocks.
    """
    _repack_ground_frames(shape)
    num_nodes = len(shape.nodes)
    rots: list = []
    trans: list = []
    for seq in shape.sequences:
        old_rots = _channel_tracks(
            shape.node_rotations,
            seq.base_rotation,
            reachable_members(seq.rotation_matters, num_nodes),
            seq.num_keyframes,
        )
        old_trans = _channel_tracks(
            shape.node_translations,
            seq.base_translation,
            reachable_members(seq.translation_matters, num_nodes),
            seq.num_keyframes,
        )
        mask = (seq.rotation_matters.mask | seq.translation_matters.mask) & ((1 << num_nodes) - 1)
        union = _with_mask(seq.rotation_matters, mask)
        base = len(rots)
        for node in union.indices():
            rots.extend(old_rots.get(node) or [shape.default_rotations[node]] * seq.num_keyframes)
            trans.extend(
                old_trans.get(node) or [shape.default_translations[node]] * seq.num_keyframes
            )
        seq.rotation_matters = union
        seq.translation_matters = _with_mask(seq.translation_matters, mask)
        seq.base_rotation = seq.base_translation = base
    # ground frames ride at the end of the node arrays in this layout, and a
    # read leaves the copies there as well as in the ground arrays
    rots.extend(shape.ground_rotations)
    trans.extend(shape.ground_translations)
    shape.node_rotations = rots
    shape.node_translations = trans


def _drop_scale_animation(shape: Shape) -> list[str]:
    if not (
        shape.node_uniform_scales
        or shape.node_aligned_scales
        or shape.node_arbitrary_scale_factors
        or any(seq.flags & SEQ_ANY_SCALE or seq.scale_matters.count() for seq in shape.sequences)
    ):
        return []
    n = (
        len(shape.node_uniform_scales)
        + len(shape.node_aligned_scales)
        + len(shape.node_arbitrary_scale_factors)
    )
    shape.node_uniform_scales = []
    shape.node_aligned_scales = []
    shape.node_arbitrary_scale_factors = []
    shape.node_arbitrary_scale_rots = []
    for seq in shape.sequences:
        seq.flags &= ~SEQ_ANY_SCALE
        seq.scale_matters = TSIntegerSet()
        seq.base_scale = 0
    return [
        f"v21 and older have no node-scale animation: dropped {n} scale key(s).  "
        f"Nodes that scaled over time now hold their rest size -- export as v22 "
        f"or newer to keep them"
    ]


def _clear_encoded_normals(shape: Shape) -> None:
    """Pre-v22 files carry no encoded-normal bytes; they are derived from the
    normals on write, so clearing them loses nothing."""
    for mesh in shape.meshes:
        if mesh is not None:
            mesh.encoded_norms = b""
            mesh.initial_encoded_norms = b""


# ----------------------------------------------------------------------
# what the flat-stream (pre-v19) format leaves out
# ----------------------------------------------------------------------


def _drop_merge_indices(shape: Shape) -> list[str]:
    n = sum(len(m.merge_indices) for m in shape.meshes if m is not None)
    if not n:
        return []
    for mesh in shape.meshes:
        if mesh is not None:
            mesh.merge_indices = []
    return [
        f"v18 and older have no merge-index table: dropped {n} entry(s).  "
        f"Detail levels of the affected meshes will pop rather than morph"
    ]


def _drop_detail_errors(shape: Shape) -> list[str]:
    if not any(d.average_error != -1.0 or d.max_error != -1.0 for d in shape.details):
        # poly counts alone are not worth a warning: the engine recomputes
        # every one of them in TSShape::init (tsShape.cc:337)
        for d in shape.details:
            d.poly_count = 0
        return []
    for d in shape.details:
        d.average_error = -1.0
        d.max_error = -1.0
        d.poly_count = 0
    return [
        "v18 and older store no LOD error metrics: dropped the average/max "
        "error of every detail.  The engine falls back to picking details by "
        "size, which is what shapes of that vintage did anyway"
    ]


def _drop_decal_texgens(shape: Shape) -> list[str]:
    n = 0
    for mesh in shape.meshes:
        if mesh is None or mesh.decal_data is None:
            continue
        n += len(mesh.decal_data.texgen_s)
        mesh.decal_data.texgen_s = []
        mesh.decal_data.texgen_t = []
    if not n:
        return []
    return [
        f"v18 and older have no decal texture-generation planes: dropped {n} "
        f"of them.  The decals are still in the file, but nothing says how to "
        f"project a texture onto them -- export as v19 or newer to keep the "
        f"projection"
    ]


def _flatten_mesh_sharing(shape: Shape) -> None:
    """Pre-v19 meshes have no parentMesh field, so each detail level carries its
    own copy of the vertices.  The arrays are already whole in memory (the
    reader copies a child's slice out of its parent), so this only forgets that
    they were shared -- at the cost of file size, not data."""
    for mesh in shape.meshes:
        if mesh is not None:
            mesh.parent_mesh = -1


def _recompute_derived(shape: Shape) -> list[str]:
    """Recompute what a pre-v19 file makes the loader work out for itself."""
    for mesh in shape.meshes:
        if mesh is not None and mesh.mesh_type != DECAL_MESH:
            compute_mesh_bounds(mesh)

    size, dl = 0.0, 0
    for i, d in enumerate(shape.details):
        if int(d.size) >= 0:
            size, dl = float(int(d.size)), i
    shape.smallest_visible_size = size
    shape.smallest_visible_dl = dl

    for node in shape.nodes:
        node.runtime = (-1, -1, -1)
    for obj in shape.objects:
        obj.runtime = (-1, -1)
    for decal in shape.decals:
        decal.raw = decal.raw[:4] + (-1,)
    for ifl in shape.ifl_materials:
        ifl.raw = ifl.raw[:2] + (0, 0, 0)

    # sub-shape counts are derived from the firsts, not stored
    warnings = []
    for firsts, nums, total, what in (
        (shape.sub_shape_first_node, shape.sub_shape_num_nodes, len(shape.nodes), "node"),
        (shape.sub_shape_first_object, shape.sub_shape_num_objects, len(shape.objects), "object"),
        (shape.sub_shape_first_decal, shape.sub_shape_num_decals, len(shape.decals), "decal"),
    ):
        derived = _counts_from_firsts(firsts, total)
        if derived != nums:
            warnings.append(
                f"v18 and older derive sub-shape {what} counts from the start "
                f"indices: {nums} became {derived}"
            )
            nums[:] = derived
    return warnings


def _counts_from_firsts(firsts: list[int], total: int) -> list[int]:
    """tsShapeOldRead.cc:508 — each sub-shape runs to the next one's start."""
    nums = [0] * len(firsts)
    prev = total
    for i in range(len(firsts) - 1, -1, -1):
        nums[i] = prev - firsts[i]
        prev = firsts[i]
    return nums


def _drop_reflection_amounts(shape: Shape) -> list[str]:
    """Reflection amount arrived in v21; below it the engine assumes 1.0."""
    n = sum(1 for m in shape.materials if m.reflection_amount != 1.0)
    if not n:
        return []
    for m in shape.materials:
        m.reflection_amount = 1.0
    return [
        f"v20 and older store no per-material reflection amount: {n} "
        f"material(s) went back to a full-strength reflection"
    ]


def _force_never_env_map(shape: Shape) -> None:
    """Pre-v16 the loader turns environment mapping off on every translucent
    material (tsMaterialList.cc:240), so a written file comes back with the flag
    whether or not it went in with it."""
    for m in shape.materials:
        if m.flags & MAT_TRANSLUCENT:
            m.flags |= MAT_NEVER_ENV_MAP


def _rebase_for_keyframe_table(shape: Shape) -> None:
    """Pre-v17 sequences have no base indices of their own: they come from the
    keyframe table, and the reader zeroes the ones no channel uses
    (rearrangeKeyframeData, tsShapeOldRead.cc:697).  Mirror that here so the
    written file reads back as this shape."""
    num_nodes = len(shape.nodes)
    for seq in shape.sequences:
        if not seq.num_keyframes:
            seq.base_rotation = seq.base_translation = -num_nodes
            seq.base_object_state = 0
            seq.base_decal_state = 0
            continue
        if not seq.rotation_matters.count():
            seq.base_rotation = seq.base_translation = -num_nodes
        if not (seq.frame_matters.mask | seq.mat_frame_matters.mask | seq.vis_matters.mask):
            seq.base_object_state = 0
        if not seq.decal_matters.count():
            seq.base_decal_state = 0


# ----------------------------------------------------------------------
# the whole job
# ----------------------------------------------------------------------


def fit_to_version(shape: Shape, version: int) -> list[str]:
    """Drop what `version` has no storage for, and say what that cost.

    Mutates the shape and returns one message per thing dropped, for the
    exporter to put in front of the user.  Afterwards ``write_shape(shape,
    version)`` cannot refuse it, and re-reading what it writes gives this
    shape back.
    """
    check_version(version)
    warnings = []

    if version in NO_GROUND_STORAGE and shape.ground_translations:
        warnings.append(
            f"v{version} has no ground-frame storage: dropped "
            f"{len(shape.ground_translations)} ground frame(s).  Movement "
            f"animations in this file carry no ground speed -- export as v24 "
            f"(or v21 and older, which keep them in the node array) to keep them"
        )
        strip_ground_frames(shape)

    if version < 22:
        trim_matters_to_tables(shape)
        warnings.extend(_drop_scale_animation(shape))
        pair_node_tracks(shape)
        _clear_encoded_normals(shape)

    if version <= 20:
        warnings.extend(_drop_reflection_amounts(shape))

    if version < 19:
        warnings.extend(_drop_merge_indices(shape))
        warnings.extend(_drop_detail_errors(shape))
        warnings.extend(_drop_decal_texgens(shape))
        _flatten_mesh_sharing(shape)
        warnings.extend(_recompute_derived(shape))

    if version < 17:
        _rebase_for_keyframe_table(shape)

    if version < 16:
        _force_never_env_map(shape)

    return warnings


# ----------------------------------------------------------------------
# the refusals write_shape makes, in the order it makes them
# ----------------------------------------------------------------------


def check_representable(shape: Shape, version: int) -> None:
    """Raise DtsWriteError if `version` cannot hold what this shape carries."""
    check_version(version)

    if version in NO_GROUND_STORAGE and shape.ground_translations:
        raise DtsWriteError(
            f"this shape has {len(shape.ground_translations)} ground frame(s) and "
            f"version {version} has nowhere to keep them; writing it would drop the "
            f"speed off every movement animation (use strip_ground_frames or "
            f"fit_to_version first)"
        )

    if version < 22:
        if (
            shape.node_uniform_scales
            or shape.node_aligned_scales
            or shape.node_arbitrary_scale_factors
        ):
            raise DtsWriteError(
                f"this shape animates node scale and version {version} has no "
                f"storage for it; writing it would drop the scale off every "
                f"animation that uses it (use fit_to_version first)"
            )
        if not node_tracks_paired(shape):
            raise DtsWriteError(
                f"version {version} stores one rotation and one translation per "
                f"animated node, and this shape animates them separately; "
                f"fit_to_version pairs them up (it costs file size, not data)"
            )
        n_ground = len(shape.ground_translations)
        if n_ground and (
            shape.node_translations[len(shape.node_translations) - n_ground :]
            != shape.ground_translations
            or shape.node_rotations[len(shape.node_rotations) - n_ground :]
            != shape.ground_rotations
        ):
            raise DtsWriteError(
                f"version {version} keeps ground frames at the end of the node "
                f"array and this shape's are not there; fit_to_version moves them"
            )

    if version < 17:
        # the keyframe-major layout derives its channel counts from the matters
        # sets, so a bit naming something the shape does not have would scramble
        # the transpose rather than being ignored the way it is at runtime
        for i, seq in enumerate(shape.sequences):
            for attr, limit, what in (
                ("rotation_matters", len(shape.nodes), "node"),
                ("translation_matters", len(shape.nodes), "node"),
                ("vis_matters", len(shape.objects), "object"),
                ("frame_matters", len(shape.objects), "object"),
                ("mat_frame_matters", len(shape.objects), "object"),
                ("decal_matters", len(shape.decals), "decal"),
            ):
                if getattr(seq, attr).mask >> limit:
                    raise DtsWriteError(
                        f"sequence {i}'s {attr.replace('_', ' ')} names a {what} "
                        f"this shape does not have, and version {version} counts "
                        f"its animation channels off that set; fit_to_version "
                        f"clears the bit (the engine ignores it anyway)"
                    )

    if version <= 20 and any(m.reflection_amount != 1.0 for m in shape.materials):
        raise DtsWriteError(
            f"version {version} stores no per-material reflection amount and "
            f"this shape sets one; fit_to_version resets them to 1.0"
        )

    if version < 19:
        if any(m.merge_indices for m in shape.meshes if m is not None):
            raise DtsWriteError(
                f"version {version} has no merge-index table and this shape has "
                f"one; fit_to_version drops it"
            )
        if any(d.average_error != -1.0 or d.max_error != -1.0 for d in shape.details):
            raise DtsWriteError(
                f"version {version} stores no LOD error metrics and this shape "
                f"has them; fit_to_version drops them"
            )
        if any(
            m.decal_data is not None and m.decal_data.texgen_s
            for m in shape.meshes
            if m is not None
        ):
            raise DtsWriteError(
                f"version {version} has no decal texture-generation planes and "
                f"this shape has them; fit_to_version drops them"
            )


def sequences_for_version(shape: Shape, version: int) -> list[tuple]:
    """(sequence, start_keyframe) pairs with indices in `version`'s address space.

    Pre-v22 the node arrays start with the shape's default transforms, so every
    base index counts from there; the ground frames sit at the far end of the
    same array.  Pre-v17 the base indices go in the keyframe table instead, and
    each sequence needs to know where its keyframes start.
    """
    if version > 21:
        return [(seq, 0) for seq in shape.sequences]

    num_nodes = len(shape.nodes)
    ground_start = len(shape.node_translations) - len(shape.ground_translations)
    out = []
    start_keyframe = 0
    for seq in shape.sequences:
        out.append(
            (
                dataclasses.replace(
                    seq,
                    base_rotation=seq.base_rotation + num_nodes,
                    base_translation=seq.base_translation + num_nodes,
                    base_scale=0,
                    first_ground_frame=num_nodes + ground_start + seq.first_ground_frame,
                ),
                start_keyframe,
            )
        )
        start_keyframe += seq.num_keyframes
    return out

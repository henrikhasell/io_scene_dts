"""The stored rest transform, on the bone it belongs to.

This was a ``dts_node_transforms`` JSON dict on the armature, keyed by DTS node
name -- so a bone's own rest data lived somewhere else entirely, under a name
that is not always the bone's.

It exists because a quaternion and its negation are the same rotation, and the
bone-matrix round trip picks a sign freely: re-deriving these rewrites bits
without changing the pose.  Export prefers the stored values whenever the bone
still agrees with them, so a file that is imported and re-exported untouched
keeps its exact node table.

Clear ``use_stored`` and the bone's own matrix wins.
"""

from __future__ import annotations

from bpy.props import BoolProperty, FloatVectorProperty, IntVectorProperty
from bpy.types import PropertyGroup


class DtsNodeProps(PropertyGroup):
    use_stored: BoolProperty(
        name="Keep Imported Rest Transform",
        description=(
            "Export this node's rest transform exactly as the file held it, as long as "
            "the bone still agrees with it.  Clear to let the bone's own matrix decide"
        ),
        default=False,
    )
    # the raw Quat16 int16s, not floats: this is what the file stores, and
    # converting through floats is what loses the exact bits
    stored_rotation: IntVectorProperty(
        name="Rest Rotation (raw)",
        description="Quat16 x, y, z, w as stored in the file",
        size=4,
        default=(0, 0, 0, 32767),
    )
    stored_translation: FloatVectorProperty(
        name="Rest Translation",
        size=3,
        default=(0.0, 0.0, 0.0),
    )

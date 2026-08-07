"""Per-sequence tables, on the action.

Ground frames, triggers and IFL membership were JSON strings.  Blender has no
Properties tab for an action at all, so these are reached through the panels in
``ui/sequence_panel.py``, which live in the Dope Sheet and NLA sidebars.
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    IntVectorProperty,
    PointerProperty,
)
from bpy.types import PropertyGroup

from .shape import SCHEMA_VERSION

__all__ = [
    "SCHEMA_VERSION",
    "DtsGroundItem",
    "DtsIflMatterItem",
    "DtsSequenceProps",
    "DtsTriggerItem",
]

SCALE_MODES = (
    ("NONE", "None", "No node scale animation"),
    ("UNIFORM", "Uniform", "One factor per keyframe, applied to all three axes"),
    ("ALIGNED", "Aligned", "Three factors per keyframe, along the node's own axes"),
    (
        "ARBITRARY",
        "Arbitrary",
        "Factors plus an orientation for the axes to be measured along.  A bone's "
        "scale cannot express this, so it is refused on export",
    ),
)


class DtsGroundItem(PropertyGroup):
    """One ground frame: the root motion the engine applies while the sequence
    plays, which is what gives a movement animation its speed."""

    translation: FloatVectorProperty(name="Translation", size=3)
    # raw Quat16 int16s, so a ground frame round-trips bit-exactly
    rotation: IntVectorProperty(name="Rotation (raw)", size=4, default=(0, 0, 0, 32767))


class DtsTriggerItem(PropertyGroup):
    """One trigger: a numbered state the engine flips at a point in the
    sequence, which scripts hang footstep sounds and effects off."""

    state: IntProperty(
        name="State", description="Which trigger state, 1..30", min=1, max=30, default=1
    )
    on: BoolProperty(name="On", description="Switch the state on rather than off", default=True)
    invert_on_reverse: BoolProperty(
        name="Invert On Reverse",
        description="Flip the sense of this trigger when the sequence plays backwards",
        default=False,
    )
    pos: FloatProperty(
        name="Position",
        description="Where in the sequence it fires, 0..1",
        min=0.0,
        max=1.0,
        default=0.0,
    )


def _is_ifl_material(self, mat) -> bool:
    return bool(getattr(mat, "dts_material", None) and mat.dts_material.is_ifl)


class DtsIflMatterItem(PropertyGroup):
    """One IFL material this sequence advances.

    A pointer, not an index.  The file stores a bit per entry in the shape's
    IFL table, but that table is derived from the materials on export, so an
    index here would name a position nothing in Blender owns -- and it was
    drawn as a bare integer with no way to tell which entry it meant.
    """

    material: PointerProperty(name="IFL Material", type=bpy.types.Material,
                              poll=_is_ifl_material)


class DtsSequenceProps(PropertyGroup):
    schema_version: IntProperty(default=0)

    ground: CollectionProperty(type=DtsGroundItem)
    ground_index: IntProperty(default=0)

    triggers: CollectionProperty(type=DtsTriggerItem)
    triggers_index: IntProperty(default=0)

    ifl_matters: CollectionProperty(type=DtsIflMatterItem)
    ifl_matters_index: IntProperty(default=0)

    scale_mode: EnumProperty(
        name="Scale Mode",
        description="Which of the DTS node-scale forms the bones' scale channels mean",
        items=SCALE_MODES,
        default="NONE",
    )

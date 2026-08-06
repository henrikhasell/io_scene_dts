"""A decal, on the projector empty that is now the whole of it.

A decal used to arrive as one mesh object per detail level plus an empty, with
its identity spread across seven ID custom properties on each of those meshes
(``dts_decal_name``, ``dts_decal_target`` and friends).  The meshes are gone:
a decal is a projection, so it is an empty, and the faces it covers are
recomputed from that empty rather than stored.

That makes the empty the only authored form, which is why these are typed
properties on it.  Two of them, ``target`` and ``material``, are real datablock
pointers where the old code kept names -- a name is not identity, and
``dts_decal_target`` broke the moment somebody renamed a mesh.

``depth`` and ``rule`` have no counterpart in the file at all.  A TSDecalMesh
stores two planes and a list of indices; there is no third axis, so nothing
says how far behind the projector a face may sit and still be covered.  The
importer has always invented that axis (``mapping/decals.py`` ``_depth_row``).
Now that coverage is recomputed rather than stored, the invention is a decision
the user has to be able to make, so it is a property rather than a constant.
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import PropertyGroup

SCHEMA_VERSION = 1

# Measured against the 18,484 decal mesh slots in the corpus, as recall of the
# faces the shipped files actually cover:
#
#     rule      depth 0.5   depth 1.0   depth 4.0
#     centre      0.319       0.444       0.541
#     any         0.372       0.513       0.608
#     all         0.122       0.152       0.163
#
# "any" at depth 4 has the best recall, but "centre" is the default: it is the
# one that reproduces a face *selection*, so making a decal covers what you
# picked rather than that plus a ring of neighbours.  Import does not use the
# default at all -- it fits rule and depth per decal against the list the file
# carries (mapping/decals.py fit_coverage).  None is close to exact; shipped
# coverage was picked by hand, not by a rule, which is why UNSUPPORTED.md
# carries this as a known loss rather than a solved problem.
COVERAGE_RULES = (
    (
        "ANY",
        "Any Corner Inside",
        "Cover a face if any of its corners falls inside the projector square.  "
        "The most generous rule and the closest to the shipped art",
    ),
    (
        "CENTRE",
        "Centre Inside",
        "Cover a face if its centre falls inside the projector square.  Tighter "
        "than Any Corner, and it can miss a face larger than the projector",
    ),
    (
        "ALL",
        "Fully Inside",
        "Cover a face only if all of its corners fall inside the projector square.  "
        "Covers nothing on geometry coarser than the decal",
    ),
)


def _is_mesh(self, obj) -> bool:
    return obj.type == "MESH"


class DtsDecalProps(PropertyGroup):
    is_dts: BoolProperty(default=False)
    schema_version: IntProperty(default=0)

    # not ``name``: PropertyGroup already has one, and shadowing it makes the
    # empty's own datablock name and the decal's name the same field in a UIList
    decal_name: StringProperty(
        name="Decal Name",
        description=(
            "The decal's name in the file.  Not unique -- turret_tank_base gives all "
            "fourteen of its decals the same one -- so the index below is its identity"
        ),
        default="",
    )
    index: IntProperty(
        name="Index",
        description=(
            "The file's own identity for this decal.  decal_states[i] and each "
            "sequence's decal_matters bits are keyed by it, so two decals must not "
            "share one"
        ),
        default=0,
        min=0,
    )
    object_name: StringProperty(
        name="Owner Object",
        description="The DTS object this decal hangs off, by name",
        default="",
    )
    subshape: IntProperty(name="Sub-shape", default=0, min=0)

    target: PointerProperty(
        name="Target",
        description=(
            "The mesh this decal projects onto.  A pointer and not a name: export "
            "matches the decal's faces against this exact object's vertices, and a "
            "renamed mesh used to silently cover nothing"
        ),
        type=bpy.types.Object,
        poll=_is_mesh,
    )
    material: PointerProperty(
        name="Material",
        description=(
            "The decal's own material, which is not the target's.  It had nowhere to "
            "live once the decal stopped being a mesh with a material slot"
        ),
        type=bpy.types.Material,
    )

    depth: FloatProperty(
        name="Depth",
        description=(
            "How far behind the projector a face may sit and still be covered, in "
            "multiples of the projector's own half-width.  The format stores no depth "
            "axis, so this is a choice rather than a recovered value"
        ),
        default=4.0,
        min=0.0,
        soft_max=8.0,
    )
    rule: EnumProperty(
        name="Coverage",
        description="Which faces of the target the projector covers",
        items=COVERAGE_RULES,
        default="CENTRE",
    )

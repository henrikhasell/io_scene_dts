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

# Measured against the 27,243 decal mesh slots in the corpus, as recall
# (precision) of the faces the shipped files actually cover.  "facing" is the
# max_angle gate at its 90-degree default:
#
#     rule        facing on        facing off
#     centre d1  0.251 (0.432)    0.436 (0.402)
#     centre d4  0.303 (0.516)    0.521 (0.476)
#     any    d4  0.346 (0.433)    0.606 (0.392)
#     all    d4  0.092 (0.263)    0.163 (0.257)
#
# Read that carefully: as a fixed rule the facing gate buys a little precision
# for a lot of recall, and is worse on F1 (0.382 against 0.498 for centre d4).
# It earns its place because import does not apply a fixed rule -- fit_coverage
# fits rule, depth *and* angle per decal against the list the file carries, and
# 180 degrees is in that search precisely so the fit can switch facing off
# where it hurts.  Its other justification is not statistical: it is what the
# original exporter did (DECAL::MAX_ANGLE, same default), and without it a
# decal on the front of a shape also lands on the back.
#
# "centre" is the authoring default because it reproduces a face *selection*:
# making a decal covers what you picked rather than that plus a ring of
# neighbours.  Nothing here is exact -- 0.4% of slots come back identical --
# which is why UNSUPPORTED.md carries this as a known loss.
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


def _retarget(self, context) -> None:
    """Follow a retarget with the preview's object gate.

    The gate is a number baked into a node in a *material*, so a pointer
    reassigned in the panel would otherwise leave the decal drawing on the mesh
    it used to point at.  Imported deferred: mapping.decals imports this module.
    """
    from ..mapping.decals import sync_host_gate

    sync_host_gate(self.id_data)


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
        update=_retarget,
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
    max_angle: FloatProperty(
        name="Max Angle",
        description=(
            "Do not cover a face whose normal turns further than this from the "
            "projector's axis.  90 degrees rejects anything pointing away, which "
            "is what stops a decal on the front of a shape also landing on the "
            "back.  The original exporter's DECAL::MAX_ANGLE, same default"
        ),
        # plain degrees rather than subtype="ANGLE": that would make Blender
        # store and show radians, and the number here is meant to be readable
        # against the exporter's own DECAL::MAX_ANGLE
        default=90.0,
        min=0.0,
        max=180.0,
    )

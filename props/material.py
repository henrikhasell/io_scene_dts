"""Per-material settings that the shader cannot hold.

Almost everything about a DTS material either lives in ``dts_*`` ID properties
(the flag bits, the bump and detail slots) or is read straight back off the
node graph (the blend bits, and now which image is the reflectance map).  One
thing is neither: whether the reflectance map should be written *into the
diffuse texture's alpha channel* or as a texture of its own.  Both are valid
files, the shader looks identical either way, and only the author knows which
they want.

That choice is usually a property of the *shape*, not of one material -- a
shape is packed the way the engine and the artist's pipeline expect, all of it
one way -- so the answer lives on the export operator and this property is the
per-material exception to it.  ``DEFAULT`` is what a material carries unless
someone says otherwise, and it defers; the other two overrule the export box in
either direction.

It is a PropertyGroup rather than an ID property for the reason
``props/mesh.py`` gives: a checkbox can only be drawn for a property that
exists, so an ID property written only by the importer can be turned off and
never on.  A material built in a fresh scene has to be able to set this, or the
feature is not authorable -- see the four conditions in CLAUDE.md.
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    IntProperty,
    PointerProperty,
)
from bpy.types import PropertyGroup

SCHEMA_VERSION = 3

# The key `reflectance_packing` replaced.  A bool with no third state could not
# say "follow the export setting", which is what the overwhelming majority of
# materials want; props/migrate.py converts it.
LEGACY_COMBINE_KEY = "combine_reflectance"


class DtsIflFrame(PropertyGroup):
    """One line of a material's ``.ifl``: a texture and how long to hold it.

    The durations are real data, not decoration -- 27 of the corpus's 35 `.ifl`
    files vary them, from 1 to 120 -- and the *order* is the animation: frames
    repeat and run out of sequence (``jetflare00.ifl`` is 210 lines over 6
    textures, ordered 00, 03, 01, 04, 02, 05).  Sorting or de-duplicating this
    list would change what the engine draws.
    """

    image: PointerProperty(name="Texture", type=bpy.types.Image)
    duration: IntProperty(name="Hold", default=1, min=1)


class DtsMaterialProps(PropertyGroup):
    is_dts: BoolProperty(default=False)
    schema_version: IntProperty(default=0)

    is_ifl: BoolProperty(
        name="IFL Material",
        description=(
            "This material is an animated flipbook: the engine reads a .ifl file "
            "named after it and swaps the texture as the sequence runs.  Ticking "
            "this is what puts an entry in the shape's IFL table, and export "
            "writes the frames below out as that .ifl"
        ),
        default=False,
    )
    # Kept apart from is_ifl on purpose.  "This material flips" and "these are
    # its frames" are different facts: a shape whose .ifl could not be found on
    # import is still an IFL material, and losing its table entry because the
    # sidecar was missing would be a worse answer than an empty list.
    ifl_frames: CollectionProperty(type=DtsIflFrame)
    ifl_frames_index: IntProperty(default=0)

    reflectance_packing: EnumProperty(
        name="Reflectance Packing",
        description=(
            "How this material's reflectance map is written on export, when it has "
            "one.  Leave it on Follow Export Setting unless this material has to "
            "disagree with the rest of the shape"
        ),
        items=[
            (
                "DEFAULT",
                "Follow Export Setting",
                "Whatever Combine Diffuse and Reflectance is set to in the export "
                "dialog.  This is what a material carries unless someone changes it",
            ),
            (
                "COMBINE",
                "Combine",
                "Always write the reflectance map into the diffuse texture's alpha "
                "channel and point this material's reflectance slot at itself, even "
                "when the export box is off.  That packing is what every material in "
                "Tribes 2's own shapes uses",
            ),
            (
                "SEPARATE",
                "Separate",
                "Always write the reflectance as a texture of its own, with its own "
                "entry in the shape's material list, even when the export box is on",
            ),
        ],
        default="DEFAULT",
    )

"""Per-material settings that the shader cannot hold.

Almost everything about a DTS material either lives in ``dts_*`` ID properties
(the flag bits, the bump and detail slots) or is read straight back off the
node graph (the blend bits, and now which image is the reflectance map).  One
thing is neither: whether the reflectance map should be written *into the
diffuse texture's alpha channel* or as a texture of its own.  Both are valid
files, the shader looks identical either way, and only the author knows which
they want.

It is a PropertyGroup rather than an ID property for the reason
``props/mesh.py`` gives: a checkbox can only be drawn for a property that
exists, so an ID property written only by the importer can be turned off and
never on.  A material built in a fresh scene has to be able to set this, or the
feature is not authorable -- see the four conditions in CLAUDE.md.
"""

from __future__ import annotations

from bpy.props import BoolProperty, IntProperty
from bpy.types import PropertyGroup

SCHEMA_VERSION = 1


class DtsMaterialProps(PropertyGroup):
    is_dts: BoolProperty(default=False)
    schema_version: IntProperty(default=0)

    combine_reflectance: BoolProperty(
        name="Combine Diffuse and Reflectance",
        description=(
            "Write the reflectance map into the diffuse texture's alpha channel and "
            "point this material's reflectance slot at itself.  That is what every "
            "material in Tribes 2's own shapes does.  Turn it off to write the "
            "reflectance as a separate texture with its own entry in the shape's "
            "material list"
        ),
        default=True,
    )

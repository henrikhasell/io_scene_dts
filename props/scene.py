"""Where the shape is standing, which the shape does not know.

The engine's environment map comes from the mission's sky -- entry 6 of the
sky's ``.dml``, ``engine/terrain/sky.h:227`` -- and the shape's own strength
multiplier is set by whatever is drawing it (1.0 for every player and static,
``engine/game/shapeBase.cc:2484``; a slider in the SDK's shape viewer,
``engine/game/showTSShape.cc:520``).  Neither is in the ``.dts``.

So these two are the add-on's only *preview* settings: they change what the
viewport shows and they are never written to a file.  They are on the scene
because that is what they describe -- a shape imported from one mission and
dropped into another reflects the second one's sky -- and because per CLAUDE.md
a value the file cannot hold must not be smuggled onto the material as though it
could.

Both write through to the shared node group on change (``mapping/envmap.py``),
in that direction only.
"""

from __future__ import annotations

import bpy
from bpy.props import FloatProperty, PointerProperty
from bpy.types import PropertyGroup

SCHEMA_VERSION = 1


def _apply(self, context):
    from ..mapping import envmap

    envmap.apply_settings(self.id_data)


class DtsSceneProps(PropertyGroup):
    env_map_image: PointerProperty(
        name="Environment Map",
        description=(
            "The sphere map reflective materials show, standing in for the sky the "
            "engine would take it from.  Preview only -- it is not part of the shape "
            "and is never exported.  With none set, nothing reflects"
        ),
        type=bpy.types.Image,
        update=_apply,
    )
    env_map_strength: FloatProperty(
        name="Strength",
        description=(
            "Scales every material's reflection, the way the engine's shape-level "
            "environment-map alpha does.  The game uses 1.0; the SDK shape viewer "
            "makes it a slider.  Preview only"
        ),
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_apply,
    )

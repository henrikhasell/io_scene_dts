"""Per-mesh settings, on the mesh object.

These were ID custom properties written only when the bit was *set*, which
made them unauthorable: a checkbox can only be drawn for a property that
exists, so a mesh that was not already a billboard had no billboard checkbox,
and ``dts_billboard_z`` -- which no Tribes 2 shape sets -- had none anywhere.
You could turn a flag off and never on.

As a PropertyGroup they exist on every object with their defaults, so the
panel can always draw them and export always has something to read.
"""

from __future__ import annotations

from bpy.props import BoolProperty, EnumProperty, IntProperty
from bpy.types import PropertyGroup

SCHEMA_VERSION = 1

SORTED_MODES = (
    ("NONE", "Standard", "An ordinary mesh, drawn in the order its primitives are stored"),
    (
        "FLAT",
        "Sorted, Unordered",
        "A sorted mesh with one cluster covering every primitive.  Keeps the mesh type "
        "without claiming an ordering the geometry was never partitioned for",
    ),
    (
        "BSP",
        "Sorted, BSP",
        "Rebuild a back-to-front cluster tree from the geometry on export.  For "
        "translucent surfaces that need to blend in the right order",
    ),
)


class DtsMeshProps(PropertyGroup):
    is_dts: BoolProperty(default=False)
    schema_version: IntProperty(default=0)

    billboard: BoolProperty(
        name="Billboard",
        description=(
            "Always face the camera.  The engine replaces this mesh's rotation with "
            "the identity, keeping only its position and scale (tsMesh.cc:59).  "
            "Nothing in the viewport shows this"
        ),
        default=False,
    )
    billboard_z: BoolProperty(
        name="Billboard Z-Axis",
        description=(
            "Modifies Billboard rather than replacing it: only the Z axis is kept, so "
            "the mesh spins to face the camera but stays upright.  What a tree trunk "
            "or a flame wants.  Has no effect unless Billboard is also on"
        ),
        default=False,
    )
    has_detail_texture: BoolProperty(
        name="Has Detail Texture",
        description="Mesh flag bit 30.  No shape in the Tribes 2 corpus sets it",
        default=False,
    )
    use_encoded_normals: BoolProperty(
        name="Use Encoded Normals",
        description=(
            "Mesh flag bit 28: read normals from the 256-entry table rather than the "
            "float array.  No shape in the Tribes 2 corpus sets it"
        ),
        default=False,
    )
    echo_type_bits: BoolProperty(
        name="Echo Mesh Type In Flags",
        description=(
            "Repeat the mesh type in the low three bits of the flags word.  Whether an "
            "exporter did this varies per mesh -- every sorted mesh in the corpus does, "
            "and 16 of its 20 skins -- so it is recorded rather than inferred"
        ),
        default=False,
    )

    sorted_mode: EnumProperty(
        name="Sorted Mode",
        description="How this mesh's draw order is decided",
        items=SORTED_MODES,
        default="NONE",
    )
    sorted_depth: IntProperty(
        name="Tree Depth",
        description=(
            "How deep to partition.  Each level doubles the index buffer, because a "
            "subtree is emitted once per direction the walk can arrive from"
        ),
        min=0,
        max=4,
        default=2,
    )
    always_write_depth: BoolProperty(
        name="Always Write Depth",
        description="Write depth values even where the mesh is translucent",
        default=False,
    )

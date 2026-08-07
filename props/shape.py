"""Shape-level tables, on the armature object.

Four of these were JSON strings on the armature.  They are lists of records,
which is exactly what a CollectionProperty is for; as strings they could be
read only by parsing them by hand in the Python console.
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import PropertyGroup

# bumped when a conversion is added; see props/migrate.py
SCHEMA_VERSION = 1


class DtsNameItem(PropertyGroup):
    """One entry of the shape's name table.

    The order is load-bearing: every name_index in the file is an offset into
    this list, and it is not the order an exporter would rebuild it in.
    """

    name: StringProperty(name="Name")


class DtsDetailItem(PropertyGroup):
    """One detail level.

    A detail can exist with no geometry at all -- an empty collision level --
    which is why the table is kept rather than derived from the objects.
    """

    name: StringProperty(name="Name")
    sub_shape_num: IntProperty(name="Sub-shape", min=0)
    object_detail_num: IntProperty(name="Detail Number", min=0)
    size: FloatProperty(
        name="Size",
        description="Screen size at which this level takes over; negative means never drawn",
    )
    average_error: FloatProperty(name="Average Error", default=-1.0)
    max_error: FloatProperty(name="Max Error", default=-1.0)
    poly_count: IntProperty(name="Polygon Count", min=0)


class DtsMaterialRef(PropertyGroup):
    """One slot of the shape's material list.

    A real pointer rather than a name.  Names are not unique in real shapes --
    104 of the 630 corpus files reuse one -- so a name-keyed list silently
    retargets a slot when two materials share a name.
    """

    material: PointerProperty(name="Material", type=bpy.types.Material)


class DtsShapeProps(PropertyGroup):
    """Everything shape-wide, on the armature that *is* the shape."""

    is_shape: BoolProperty(
        name="Is a DTS Shape",
        description="Set on the armature an imported shape was built around",
        default=False,
    )
    schema_version: IntProperty(default=0)

    names: CollectionProperty(type=DtsNameItem)
    names_index: IntProperty(default=0)

    details: CollectionProperty(type=DtsDetailItem)
    details_index: IntProperty(default=0)

    material_order: CollectionProperty(type=DtsMaterialRef)
    material_order_index: IntProperty(default=0)


    migration_note: StringProperty(
        name="Migration Note",
        description="What was dropped when this scene was brought forward",
        default="",
    )

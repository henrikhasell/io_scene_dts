"""UILists for the DTS tables.

Each one draws a row of a CollectionProperty from props/.  They are separate
classes rather than one generic list because Blender resolves a UIList by
``bl_idname`` at draw time and the columns differ per table.
"""

from __future__ import annotations

from bpy.types import UIList


class DTS_UL_names(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        layout.prop(item, "name", text="", emboss=False, icon="SORTALPHA")


class DTS_UL_details(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False)
        sub = row.row(align=True)
        sub.alignment = "RIGHT"
        # a negative size means the level is never drawn -- collision and
        # line-of-sight meshes live there
        sub.label(text=f"size {item.size:g}" if item.size >= 0 else "not drawn")


class DTS_UL_material_order(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        row = layout.row(align=True)
        row.label(text=f"{index}")
        if item.material is not None:
            row.prop(item.material, "name", text="", emboss=False, icon="MATERIAL")
        else:
            row.label(text="(missing)", icon="ERROR")


class DTS_UL_ifl_frames(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        row = layout.row(align=True)
        row.label(text=item.image.name if item.image else "(no image)",
                  icon="TEXTURE" if item.image else "ERROR")
        sub = row.row(align=True)
        sub.alignment = "RIGHT"
        sub.label(text=f"hold {item.duration}")


class DTS_UL_ground(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        t = item.translation
        layout.label(text=f"{index}:  {t[0]:.3f}, {t[1]:.3f}, {t[2]:.3f}", icon="ANIM")


class DTS_UL_triggers(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        row = layout.row(align=True)
        row.label(text=f"state {item.state}", icon="MARKER_HLT")
        row.label(text="on" if item.on else "off")
        sub = row.row(align=True)
        sub.alignment = "RIGHT"
        sub.label(text=f"at {item.pos:.2f}")


class DTS_UL_ifl_matters(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        layout.label(text=item.material.name if item.material else "(none)",
                     icon="MATERIAL" if item.material else "ERROR")


CLASSES = (
    DTS_UL_names,
    DTS_UL_details,
    DTS_UL_material_order,
    DTS_UL_ifl_frames,
    DTS_UL_ground,
    DTS_UL_triggers,
    DTS_UL_ifl_matters,
)

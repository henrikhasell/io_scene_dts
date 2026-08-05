"""Panels, lists and operators for the DTS data.

The add-on had no UI at all before this: every value it recorded was a raw ID
custom property, and the tables were JSON strings that could only be read by
parsing them by hand.

Registration order matters -- a Panel naming a UIList by ``bl_idname`` needs it
registered first -- and nothing is imported at module scope, so importing this
package outside Blender does not fail.
"""

from __future__ import annotations


def _classes():
    from . import lists, operators, panels

    return operators.CLASSES + lists.CLASSES + panels.CLASSES


def register() -> None:
    import bpy

    for cls in _classes():
        bpy.utils.register_class(cls)


def unregister() -> None:
    import bpy

    for cls in reversed(_classes()):
        bpy.utils.unregister_class(cls)

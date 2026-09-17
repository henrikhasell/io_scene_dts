from __future__ import annotations

# Set by register(), cleared by unregister().  Module scope holds only this
# sentinel: ui/__init__.py's docstring promises the package imports outside
# Blender, so every `import bpy` below stays inside a function body.
_previews = None

ICON_DIR = "icons"
SHAPE = "logo-128"


def register() -> None:
    global _previews
    import os

    import bpy.utils.previews

    if _previews is not None:  # defensive: a double register would leak the old one
        unregister()
    _previews = bpy.utils.previews.new()
    path = os.path.join(os.path.dirname(__file__), ICON_DIR, SHAPE + ".png")
    # An unreadable icon must not take the menus down with it -- icon_id() then
    # returns 0, which Blender draws as no icon, and the entries look the way
    # they did before this module existed.
    try:
        _previews.load(SHAPE, path, "IMAGE")
    except Exception as err:  # pragma: no cover - a corrupt or missing file
        print(f"io_scene_dts: could not load {path}: {err}")


def unregister() -> None:
    global _previews
    if _previews is None:
        return
    import bpy.utils.previews

    bpy.utils.previews.remove(_previews)
    _previews = None


def icon_id(name: str = SHAPE) -> int:
    """The icon's ``icon_value``, or 0 when there isn't one.

    0 is Blender's "no icon", so every caller can pass this straight to
    ``layout.operator(..., icon_value=...)`` without checking.
    """
    if _previews is None:
        return 0
    preview = _previews.get(name)
    return preview.icon_id if preview is not None else 0

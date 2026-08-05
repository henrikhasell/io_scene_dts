"""io_scene_dts — Torque DTS/DSQ importer/exporter for Blender 4.2+.

Import/export Torque three-space shapes (.dts, versions 19-24 read; 24 and 23
written) and sequence files (.dsq), including skinning and animation.
"""

try:
    import bpy
except ModuleNotFoundError:  # imported outside Blender (pytest, tooling)
    bpy = None

if bpy is not None:
    from . import props, ui
    from .ops import export_dsq, export_dts, import_dsq, import_dts

    _CLASSES = (
        import_dts.ImportDTS,
        export_dts.ExportDTS,
        import_dsq.ImportDSQ,
        export_dsq.ExportDSQ,
    )

    def register():
        # properties first: the operators and panels below both reference the
        # groups they define
        props.register()
        for cls in _CLASSES:
            bpy.utils.register_class(cls)
        ui.register()
        bpy.types.TOPBAR_MT_file_import.append(import_dts.menu_func)
        bpy.types.TOPBAR_MT_file_import.append(import_dsq.menu_func)
        bpy.types.TOPBAR_MT_file_export.append(export_dts.menu_func)
        bpy.types.TOPBAR_MT_file_export.append(export_dsq.menu_func)

    def unregister():
        bpy.types.TOPBAR_MT_file_import.remove(import_dts.menu_func)
        bpy.types.TOPBAR_MT_file_import.remove(import_dsq.menu_func)
        bpy.types.TOPBAR_MT_file_export.remove(export_dts.menu_func)
        bpy.types.TOPBAR_MT_file_export.remove(export_dsq.menu_func)
        ui.unregister()
        for cls in reversed(_CLASSES):
            bpy.utils.unregister_class(cls)
        props.unregister()

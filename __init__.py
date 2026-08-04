"""io_scene_dts — Torque DTS/DSQ importer/exporter for Blender 4.2+.

Import/export Torque three-space shapes (.dts, versions 19-24 read; 24 and 23
written) and sequence files (.dsq), including skinning and animation.
"""

try:
    import bpy
except ModuleNotFoundError:  # imported outside Blender (pytest, tooling)
    bpy = None

if bpy is not None:
    from .ops import export_dsq, export_dts, import_dsq, import_dts, nla_stack

    _CLASSES = (
        import_dts.ImportDTS,
        export_dts.ExportDTS,
        import_dsq.ImportDSQ,
        export_dsq.ExportDSQ,
        nla_stack.StackSequencesNLA,
    )

    def register():
        for cls in _CLASSES:
            bpy.utils.register_class(cls)
        bpy.types.TOPBAR_MT_file_import.append(import_dts.menu_func)
        bpy.types.TOPBAR_MT_file_import.append(import_dsq.menu_func)
        bpy.types.TOPBAR_MT_file_export.append(export_dts.menu_func)
        bpy.types.TOPBAR_MT_file_export.append(export_dsq.menu_func)
        bpy.types.NLA_MT_add.append(nla_stack.menu_func)

    def unregister():
        bpy.types.NLA_MT_add.remove(nla_stack.menu_func)
        bpy.types.TOPBAR_MT_file_import.remove(import_dts.menu_func)
        bpy.types.TOPBAR_MT_file_import.remove(import_dsq.menu_func)
        bpy.types.TOPBAR_MT_file_export.remove(export_dts.menu_func)
        bpy.types.TOPBAR_MT_file_export.remove(export_dsq.menu_func)
        for cls in reversed(_CLASSES):
            bpy.utils.unregister_class(cls)

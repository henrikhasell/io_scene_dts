#!/usr/bin/env python3
"""Break the export path on purpose and check the right test notices.

CLAUDE.md: "A round-trip test that passes on its first run deserves a mutation
check -- because a test that reads back what it never wrote will pass for the
wrong reason."  That risk is real here: import and export share the property
names, so a test can assert a value survives when in truth neither end ever
touched the file.

Each mutation is a one-line edit that disables exactly one capability, paired
with the test that exists to catch it.  The mutation is applied to a *copy* of
the checkout in a temp directory -- the working tree is never modified -- and
the run passes when the named tests fail.

Usage:
    scripts/mutate.py --list
    scripts/mutate.py                # every mutation
    scripts/mutate.py sorted-mode    # one
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# mutations whose tests live in the pytest suite rather than inside Blender
RUNNERS = {
    "sorted-threading": "pytest",
    "decal-material-counts-as-translucent": "pytest",
    "mask-reduced-by-red": "pytest",
}

# name -> (file, find, replace, tests that must fail)
MUTATIONS = {
    # Until every mesh was re-derived unconditionally this mutation narrowed
    # mesh_digest instead, because the digest was what decided whether a UV
    # edit survived.  It stopped being caught the moment the payload stopped
    # gating ordinary meshes -- which is the harness doing its job.
    "uv-export": (
        "mapping/blender_to_shape.py",
        "            uv = tuple(uv_layer.data[loop_index].uv) if uv_layer else (0.0, 0.0)",
        "            uv = (0.0, 0.0)",
        ["test_uv_edit_reaches_the_exported_file"],
    ),
    "lod-pool": (
        "mapping/blender_to_shape.py",
        "            warnings, pool=pool,",
        "            warnings, pool=None,",
        ["test_lod_vertex_sharing_is_rederived"],
    ),
    # Back to a Principled per decal.  Renders identically -- which is the
    # point: nothing about the picture says the material now costs N+1 BSDFs
    # per pixel instead of one.
    "decal-shader-per-decal": (
        "mapping/decals.py",
        "    if not _decal_is_unlit(props.material):",
        "    if False:",
        ["test_a_decal_previews_lit_the_way_the_engine_lights_it"],
    ),
    # The splice tag that says which input carried the host's own signal.
    # Without it the upstream walk follows the decal's *factor* into the decal's
    # own texture, and the material exports pointing at the decal's image.
    "decal-passthrough-tag": (
        "mapping/decals.py",
        "    blend[PASSTHROUGH_INPUT] = list(blend.inputs).index(sockets[1])",
        "",
        ["test_a_decal_does_not_rename_its_hosts_texture"],
    ),
    # Letting the environment map keep reflecting through a decal.
    "decal-reflection-not-damped": (
        "mapping/decals.py",
        "            _damp_reflection(nt, target_mat, factor, label, (x - 200, y - 700))",
        "            pass",
        ["test_a_decal_takes_the_reflection_off_what_it_covers"],
    ),
    # Going back to starting sequence 0 on import.  Silent when it regresses --
    # the shape animates, it just animates something nobody asked for, and the
    # NLA sums it under whatever the user unmutes next.
    "import-plays-sequence-zero": (
        "mapping/nla.py",
        "def stack_actions(arm_obj, actions, fps: float, keep_playing=NOTHING_PLAYING):",
        "def stack_actions(arm_obj, actions, fps: float, keep_playing=None):",
        ["test_dts_import_stacks_sequences_as_nla_strips",
         "test_import_dts_with_dsq_companions"],
    ),
    # ...and the other half: muting so eagerly that a .dsq aimed at an armature
    # by hand arrives silent too, which reads as the import having failed.
    "dsq-import-plays-nothing": (
        "ops/import_dsq.py",
        "            keep_playing=actions[0].name if actions else None,",
        "",
        ["test_dsq_onto_an_existing_rig_plays_what_you_just_loaded"],
    ),
    # A decal is an empty now, so the "dts_decal_name" guard in
    # _gather_mesh_objects is dead for any migrated scene -- mutating it caught
    # nothing, which the harness reported.  What still protects against phantom
    # decal objects is migration deleting the legacy meshes, so that is what
    # this mutates instead.
    "decal-objects": (
        "props/migrate.py",
        "        bpy.data.objects.remove(mesh)",
        "        pass",
        ["test_legacy_decal_meshes_migrate_to_their_empty"],
    ),
    "matframes-store": (
        "mapping/matframes.py",
        "    if mesh.num_mat_frames <= 1 or not mesh.tverts:\n        return 0",
        "    if True:\n        return 0",
        ["test_matframes_survive_an_edit"],
    ),
    "matframes-export": (
        "mapping/blender_to_shape.py",
        "    for block in matframes.extra_blocks(me, blender_vert_per_dts_vert):",
        "    for block in []:",
        ["test_matframes_survive_an_edit"],
    ),
    "merge-indices": (
        "mapping/blender_to_shape.py",
        "    merge = bobj.get(\"dts_merge_indices\")",
        "    merge = None",
        ["test_merge_indices_survive_an_edit"],
    ),
    # checked by the fast pytest loop rather than inside Blender
    "sorted-threading": (
        "dtslib/sorted_build.py",
        "        back_entry = emit(front, emit(back, continuation, remaining - 1), remaining - 1)",
        "        back_entry = front_entry",
        ["test_beats_the_shipped_median"],
    ),
    # test_sorted_meshes_survive_an_edit used to catch this by selecting the
    # objects that recorded a mode.  It cannot any more -- a translucent sorted
    # mesh records nothing now, because export infers it -- so the mutation is
    # pointed at the test that asserts the stored value directly.
    "sorted-mode": (
        "mapping/shape_to_blender.py",
        '    bobj.dts_mesh.sorted_mode = "BSP"',
        '    bobj.dts_mesh.sorted_mode = "NONE"',
        ["test_an_opaque_sorted_mesh_still_records_its_mode"],
    ),
    "fresh-sorted": (
        "mapping/blender_to_shape.py",
        "        mesh.mesh_type = SORTED_MESH",
        "        mesh.mesh_type = STANDARD_MESH",
        ["test_a_sorted_mesh_is_authorable", "test_flat_sorted_mode_is_authorable"],
    ),
    "object-state-read": (
        "mapping/objectstate.py",
        "        parsed = parse_path(fcurve.data_path)\n        if parsed is None:\n            continue",
        "        parsed = None\n        if parsed is None:\n            continue",
        ["test_keyframed_visibility_reaches_the_exported_file"],
    ),
    "scale-export": (
        "mapping/sequences.py",
        '            if "scale" in props and bone in node_index_by_bone',
        "            if False",
        ["test_editing_a_scale_key_reaches_the_file"],
    ),
    "keyframe-count": (
        "mapping/sequences.py",
        "            last = max(last, fc.keyframe_points[-1].co[0])\n    return int(round(last))",
        "            last = max(last, fc.keyframe_points[-1].co[0])\n    return int(round(last)) + 1",
        ["test_removing_a_keyframe_shortens_the_sequence"],
    ),
    # Disabling the scale is invisible to any test that only checks a texture
    # was written -- the file is still there, just the wrong size -- so the
    # authoring test reads the written PNG's dimensions back off disk.
    "texture-power-of-two": (
        "mapping/texture_io.py",
        "    width, height = size\n    if power_of_two:",
        "    width, height = size\n    if False:",
        [
            "test_textures_are_scaled_to_a_power_of_two_on_export",
            "test_the_export_size_rule",
        ],
    ),
    "texture-size-limit": (
        "mapping/texture_io.py",
        "    if max_size and max(width, height) > max_size:",
        "    if False:",
        [
            "test_a_texture_larger_than_512_is_scaled_down_on_export",
            "test_the_export_size_rule",
        ],
    ),
    # the cap has to divide both sides, not clamp the long one: clamping turns
    # a 1024x256 texture into 512x256 and stretches the art 2:1, which nothing
    # downstream records and no in-game look would obviously blame on export
    "texture-size-limit-aspect": (
        "mapping/texture_io.py",
        "        factor = max_size / max(width, height)\n"
        "        width, height = max(1, round(width * factor)), max(1, round(height * factor))",
        "        width, height = min(width, max_size), min(height, max_size)",
        [
            "test_a_texture_larger_than_512_is_scaled_down_on_export",
            "test_the_export_size_rule",
        ],
    ),
    # Not splitting is invisible to anything that only checks the decal draws:
    # it still draws, on a material 25 meshes share, and only the frame rate
    # says so.  The authoring test asserts the branch is in the target's own
    # copy and that the shared material has none.
    "decal-material-split": (
        "mapping/decals.py",
        '    if mat.get("dts_decal_host") == target_obj.name:\n        return mat',
        "    if True:\n        return mat",
        ["test_a_decal_previews_only_on_its_target"],
    ),
    # And the other half: if the copies do not collapse on dts_name, one shape
    # exports 17 material entries where the file had 1.
    "material-dedupe-by-name": (
        "mapping/blender_to_shape.py",
        "        if m is bmat or _dts_material_key(m) == key:",
        "        if m is bmat:",
        ["test_decals_roundtrip_through_their_projectors"],
    ),
    # sync_host_gate must only ever *move* a branch.  Letting it build one puts
    # an imageless branch on every decal at import, which renders pink and is
    # invisible to any assertion about the branch's shape.
    "decal-branch-not-built-on-retarget": (
        "mapping/decals.py",
        "        if moved and not any(n.label == label for n in wanted.node_tree.nodes):",
        "        if not any(n.label == label for n in wanted.node_tree.nodes):",
        ["test_a_decal_branch_projects_the_decals_own_image"],
    ),
    "material-flag-bits": (
        "mapping/materials.py",
        '    "dts_bump_map_only": MAT_BUMP_MAP_ONLY,',
        "",
        ["test_every_material_flag_bit_has_a_checkbox"],
    ),
    "legacy-guard": (
        "mapping/blender_to_shape.py",
        "    stale = migrate.legacy_keys_present()",
        "    stale = []",
        ["test_export_refuses_a_scene_that_has_not_been_converted"],
    ),
    "dsq-ground": (
        "mapping/dsq.py",
        "            for item in action.dts_sequence_props.ground",
        "            for item in []",
        ["test_dsq_sequences_use_the_same_tables_as_dts_ones"],
    ),
    # --- paths only the fresh-scene suite reaches -------------------------
    "fresh-material-defaults": (
        "mapping/materials.py",
        "        flags = MAT_S_WRAP | MAT_T_WRAP",
        "        flags = 0",
        ["test_a_material_reaches_the_file"],
    ),
    "fresh-reflectance-selfindex": (
        "mapping/materials.py",
        "            mat.reflectance_map = i\n            mat.bump_map = NO_MAP",
        "            mat.reflectance_map = NO_MAP\n            mat.bump_map = NO_MAP",
        ["test_a_material_without_maps_gets_engine_safe_defaults"],
    ),
    # -- reflectance maps -----------------------------------------------
    "reflectance-import": (
        "mapping/materials.py",
        '        envmap.wire(bmat, reflectance.outputs["Color"])',
        "        pass",
        ["test_a_self_reflectance_imports_as_two_images"],
    ),
    # -- the environment map ----------------------------------------------
    # The one mutation that earns the render test.  Without the Z flip the
    # group computes a sphere map in Blender's camera space instead of GL's
    # eye space -- a graph that still builds, still exports the same bytes, and
    # reflects the wrong half of the environment.  Nothing that reads a file
    # back can see it.
    "envmap-camera-space": (
        "mapping/envmap.py",
        '    eye_gl.inputs[1].default_value = (1.0, 1.0, -1.0)',
        "    eye_gl.inputs[1].default_value = (1.0, 1.0, 1.0)",
        ["test_the_group_computes_the_engines_sphere_map"],
    ),
    "envmap-normal-space": (
        "mapping/envmap.py",
        '    normal_gl.inputs[1].default_value = (1.0, 1.0, -1.0)',
        "    normal_gl.inputs[1].default_value = (1.0, 1.0, 1.0)",
        ["test_the_group_computes_the_engines_sphere_map"],
    ),
    # the reflectance is read off the group now; if that lookup goes, every
    # material authored the new way silently exports with no reflectance at all
    "envmap-mask-anchor": (
        "mapping/materials.py",
        '        return _image_node_upstream(group.inputs["Mask"])',
        "        return None",
        ["test_a_reflectance_map_is_authorable"],
    ),
    # an environment map with no image must read as "do not reflect", not as
    # "reflect black" -- the difference between an unset scene looking untouched
    # and every reflective material going dark
    "envmap-unset-strength": (
        "mapping/envmap.py",
        "        value.outputs[0].default_value = strength if image is not None else 0.0",
        "        value.outputs[0].default_value = strength",
        ["test_no_environment_image_means_no_reflection"],
    ),
    # a decal is lit by the engine unless its material says otherwise; the
    # unconditional Emission this replaced made every decal preview as though
    # it were self-illuminating
    # Swap which decals get a surface of their own.  Catches both directions at
    # once: a lit decal previewing as an emitter, and an unlit one previewing as
    # a colour the host's Principled shades -- the second is the failure the
    # collapse could newly introduce.
    "decal-preview-unlit": (
        "mapping/decals.py",
        "    if not _decal_is_unlit(props.material):",
        "    if _decal_is_unlit(props.material):",
        ["test_a_decal_previews_lit_the_way_the_engine_lights_it",
         "test_a_self_illuminating_decal_previews_unlit"],
    ),
    "decal-preview-ignores-self-illumination": (
        "mapping/decals.py",
        '    if mat.get("dts_self_illuminating"):\n        return True',
        '    if False:\n        return True',
        ["test_a_self_illuminating_decal_previews_unlit"],
    ),
    # the exporter reduced a colour mask by its red channel while the shader
    # reduced it by luminance, so a hand-painted off-grey mask exported as a
    # different mask than the viewport was showing
    "mask-reduced-by-red": (
        "mapping/texture_split.py",
        "    if red == green == blue:",
        "    if True:",
        ["test_merge_reduces_a_colour_mask_by_luminance"],
    ),
    # the rebuild has to *remove* the old branch first: wire_decal_branch
    # refuses a label it already finds, so without the removal the operator
    # reports success and changes nothing
    "decal-rebuild-keeps-the-old-branch": (
        "mapping/decals.py",
        "            remove_decal_branch(nt, label)\n            rebuilt = True",
        "            rebuilt = True",
        ["test_rebuilding_a_decal_preview_relights_an_old_branch"],
    ),
    "reflection-amount-export": (
        "mapping/materials.py",
        "                reflection_amount=float(bmat.dts_material.reflection_amount),",
        "                reflection_amount=1.0,",
        ["test_reflection_amount_is_authorable"],
    ),
    "reflection-amount-migration-keeps-old-key": (
        "props/migrate.py",
        "    del mat[LEGACY_REFLECTION_AMOUNT_KEY]",
        "    pass",
        ["test_migration_converts_the_old_reflection_amount"],
    ),
    # the gate in the *permissive* direction: without the env-map check a
    # translucent material's alpha would be read as a reflectance mask too,
    # which is the one thing the disambiguation rule exists to prevent
    "reflectance-alpha-gate": (
        "mapping/materials.py",
        "    if mat.flags & MAT_NEVER_ENV_MAP or mat.flags & (MAT_ADDITIVE | MAT_SUBTRACTIVE):",
        "    if mat.flags & (MAT_ADDITIVE | MAT_SUBTRACTIVE):",
        ["test_uv_and_alpha"],
    ),
    "fresh-reflectance-entry": (
        "mapping/materials.py",
        "        index = len(mats)",
        "        index = 0",
        ["test_a_reflectance_map_is_authorable"],
    ),
    # -- decals baked as meshes -------------------------------------------
    # the checkbox pinned off: decals go back to being TSDecalMeshes, which is
    # a valid file and the wrong one
    "bake-decals-ignored": (
        "mapping/blender_to_shape.py",
        "        if decals_as_meshes:\n            baked_decals.update(",
        "        if False:\n            baked_decals.update(",
        [
            "test_a_decal_is_authorable_as_a_baked_mesh",
            "test_a_shipped_shapes_decals_can_export_as_meshes",
        ],
    ),
    # baked and projected at once -- the thing the "baked meshes only" answer
    # rules out, because a decal-aware engine then draws the art twice
    "bake-decals-also-projects": (
        "mapping/blender_to_shape.py",
        "    decal_index_map = {}\n    if decals_as_meshes:",
        "    decal_index_map = {}\n    if False:",
        ["test_a_decal_is_authorable_as_a_baked_mesh"],
    ),
    # the lift: without it the baked mesh is coplanar with its target and
    # z-fights, which no structural assertion about the file would catch
    "bake-decals-not-lifted": (
        "mapping/decals.py",
        "                tuple(to_dts @ co + n * DECAL_LIFT),",
        "                tuple(to_dts @ co),",
        ["test_a_baked_decal_is_lifted_off_its_target"],
    ),
    # the UVs: a baked decal with no projection samples one texel of its
    # texture and renders as a flat colour
    "bake-decals-no-texgen": (
        "mapping/decals.py",
        "            uv = (\n                co[0] * s[0] + co[1] * s[1] + co[2] * s[2] + s[3],",
        "            uv = (\n                0.0 * s[0] + 0.0 * s[1] + 0.0 * s[2] + s[3],",
        ["test_a_decal_is_authorable_as_a_baked_mesh"],
    ),
    # the state track: the geometry is there and nothing switches it, so every
    # scorch mark in a Damage sequence is on from the first frame
    "bake-decals-lose-state-track": (
        "mapping/sequences.py",
        "            tracked.append((oi, {\"vis\": vis}))\n            vis_set.set(oi)",
        "            pass",
        ["test_a_baked_decals_state_track_becomes_object_visibility"],
    ),
    # ...and the rest state, which decides whether it is on before any sequence
    "bake-decals-rest-state": (
        "mapping/blender_to_shape.py",
        "            shape.object_states.append(ObjectState(baked_vis[i], 0, 0))",
        "            shape.object_states.append(ObjectState(1.0, 0, 0))",
        ["test_a_baked_decals_state_track_becomes_object_visibility"],
    ),
    # the export checkbox itself: pinned on, and the shape-wide "separate" the
    # user asked for silently becomes the combined packing instead
    "export-box-ignored": (
        "mapping/materials.py",
        "    return combine_default",
        "    return True",
        ["test_unticking_the_export_box_writes_two_textures"],
    ),
    # ...and the two per-material exceptions to it, each in its own direction.
    # A material set to COMBINE or SEPARATE that quietly follows the box is a
    # checkbox that does nothing, which is what DEFAULT is already for.
    "material-combine-override-ignored": (
        "mapping/materials.py",
        '    if packing == "COMBINE":',
        "    if False:",
        ["test_a_material_overrules_the_export_box_in_either_direction"],
    ),
    "material-separate-override-ignored": (
        "mapping/materials.py",
        '    if packing == "SEPARATE":',
        "    if False:",
        [
            "test_a_material_overrules_the_export_box_in_either_direction",
            "test_a_cross_referenced_reflectance_imports_as_the_other_materials_texture",
        ],
    ),
    # an imported cross-reference has to record SEPARATE, or re-exporting it
    # with the box on folds a texture several materials share into one alpha
    "cross-reference-packing-unrecorded": (
        "mapping/materials.py",
        '                props.reflectance_packing = "SEPARATE"',
        "                pass",
        ["test_a_cross_referenced_reflectance_imports_as_the_other_materials_texture"],
    ),
    # the old bool, converted the wrong way: a material that asked for its own
    # texture quietly starts following a ticked box instead
    "combine-migration-flattened": (
        "props/migrate.py",
        '    props.reflectance_packing = "DEFAULT" if bool(value) else "SEPARATE"',
        '    props.reflectance_packing = "DEFAULT"',
        ["test_migration_converts_the_old_combine_checkbox"],
    ),
    "combine-migration-keeps-old-key": (
        "props/migrate.py",
        "    del props[LEGACY_COMBINE_KEY]",
        "    pass",
        ["test_migration_converts_the_old_combine_checkbox"],
    ),
    "reflectance-forces-envmap": (
        "mapping/materials.py",
        "            mat.flags &= ~MAT_NEVER_ENV_MAP",
        "            pass",
        ["test_a_reflectance_material_is_env_mapped"],
    ),
    # every texture is copied now, not just the ones with no file behind them.
    # This restores the old gate, which is the exact bug the change was made to
    # fix: a shape exported to a mod tree with none of its art.
    "texture-copies-file-backed": (
        "mapping/materials.py",
        "        if i in handled or node is None or node.image is None:",
        "        if i in handled or node is None or node.image is None or node.image.filepath:",
        ["test_a_texture_loaded_from_disk_is_copied_beside_the_dts"],
    ),
    # the ordinary diffuse loop above is not the only way out: an env-mapped
    # material -- which is most of a real shape -- writes its texture as the
    # recombined pair instead, and used to skip that when the split was
    # untouched.  Every shipped material this add-on is aimed at goes this way.
    "texture-copies-recombined": (
        "mapping/materials.py",
        "            writes.append(TextureWrite(merged, _png_name(mat.name), mat.name))",
        "            pass",
        [
            "test_export_copies_an_imported_texture_beside_the_dts",
            "test_a_reflectance_round_trips_byte_identically",
        ],
    ),
    # ...and the checkbox is the only thing that stops it, so it has to bite.
    # `image`, not `write.image`: the save takes whichever the size rule left,
    # the original or the resized copy, and anchoring on the field this reads
    # from is what let the mutation go stale when that variable appeared
    "texture-overwrite": (
        "mapping/texture_io.py",
        "                    image.save(filepath=str(target))",
        "                    pass",
        ["test_export_overwrites_a_source_texture"],
    ),
    "sorted-promote-translucent": (
        "mapping/blender_to_shape.py",
        '    promoted = mode == "NONE" and is_translucent(bobj)',
        "    promoted = False",
        [
            "test_a_translucent_mesh_is_promoted_to_a_sorted_one",
            "test_an_imported_translucent_mesh_is_promoted_on_re_export",
        ],
    ),
    # the other direction: promoting everything would make the rule vacuous
    "sorted-promote-gate": (
        "mapping/materials.py",
        "        slot.material is not None and blend_flags_from_material(slot.material) & MAT_TRANSLUCENT",
        "        slot.material is not None",
        ["test_an_opaque_mesh_is_left_standard"],
    ),
    # the importer must not write down what export already infers...
    "sorted-mode-not-stored": (
        "mapping/shape_to_blender.py",
        "    if not is_translucent(bobj):\n        bobj.dts_mesh.sorted_mode = \"BSP\"",
        '    if True:\n        bobj.dts_mesh.sorted_mode = "BSP"',
        ["test_a_translucent_sorted_mesh_records_no_mode"],
    ),
    # the other direction is `sorted-mode` above, which breaks the value
    # stored for a mesh that genuinely needs one
    "blend-props-not-stored": (
        "props/migrate.py",
        "            if key in mat:\n                del mat[key]",
        "            if False:\n                del mat[key]",
        ["test_migration_drops_the_blend_props_saved_beside_the_shader"],
    ),
    # -- IFL: the material's flipbook ------------------------------------
    # There were no IFL mutations at all before this, so none of the three
    # tests that claimed to cover it was ever checked for biting.
    "ifl-frames-read": (
        "mapping/materials.py",
        "    for name, duration in parse_ifl(text):",
        "    for name, duration in []:",
        ["test_an_ifl_imports_its_frames_and_previews_them"],
    ),
    "ifl-preview": (
        "mapping/materials.py",
        '        value.outputs[0].keyframe_insert("default_value", frame=tick + 1)',
        "        pass",
        ["test_an_ifl_imports_its_frames_and_previews_them"],
    ),
    # the table is derived; deriving nothing must be caught
    "ifl-entry-derived": (
        "mapping/materials.py",
        "        if props is None or not props.is_ifl:",
        "        if True:",
        ["test_an_ifl_material_is_authorable", "test_ifl_preserved"],
    ),
    "ifl-flag-derived": (
        "mapping/materials.py",
        "    if props is not None and props.is_ifl:",
        "    if False:",
        ["test_an_ifl_material_is_authorable"],
    ),
    "ifl-sidecar-write": (
        "mapping/materials.py",
        "        writes.append(\n            TextureWrite(format_ifl(lines), Material(name=ifl_name).basename, dts_name)\n        )",
        "        pass",
        ["test_an_ifl_material_is_authorable"],
    ),
    # the order of the frames is the animation
    "ifl-frame-order": (
        "mapping/ifl.py",
        "        frames.append((name, max(1, duration)))",
        "        frames.insert(0, (name, max(1, duration)))",
        ["test_an_ifl_round_trips_through_its_material"],
    ),
    "ifl-matters-resolve": (
        "mapping/sequences.py",
        "            iset.set(index)",
        "            pass",
        ["test_ifl_membership_is_authorable"],
    ),
    # the checkbox must actually gate images, and must not gate the .ifl
    "export-textures-gate": (
        "mapping/texture_io.py",
        "        if not include_images and not isinstance(write.image, str):",
        "        if False:",
        ["test_export_textures_gates_images_but_not_the_ifl"],
    ),
    "fresh-winding": (
        "mapping/blender_to_shape.py",
        "        a, b, c = (corner_index[li] for li in reversed(tri.loops))",
        "        a, b, c = (corner_index[li] for li in tri.loops)",
        ["test_triangle_winding_is_clockwise_front"],
    ),
    "fresh-skin": (
        "mapping/blender_to_shape.py",
        "    is_skin = bool(bobj.vertex_groups) and any(",
        "    is_skin = False and any(",
        ["test_a_skinned_mesh_is_authorable"],
    ),
    "fresh-decal": (
        "mapping/decals.py",
        "    projectors = {d.dts_decal.index: d for d in decal_objects()}",
        "    projectors = {}",
        ["test_a_decal_is_authorable_by_hand"],
    ),
    "fresh-lod-sharing": (
        "mapping/blender_to_shape.py",
        "            and mesh.num_frames == 1",
        "            and False",
        ["test_lod_meshes_share_a_vertex_array"],
    ),
    "decal-texgen": (
        "mapping/decals.py",
        "            s, t = projector_to_texgen(projector.matrix_world, bobj.matrix_world)",
        "            s, t = projector_to_texgen(Matrix.Identity(4), bobj.matrix_world)",
        ["test_moving_the_projector_moves_the_decal"],
    ),
    # coverage is derived now, so this is the mutation that matters most: if
    # covered_faces stops finding faces there is no stored list to fall back on
    "decal-coverage": (
        "mapping/decals.py",
        "        if inside:\n            hits.append(polygon.index)",
        "        if False:\n            hits.append(polygon.index)",
        [
            "test_an_operator_decal_covers_every_detail_level",
            "test_decal_coverage_is_what_the_export_writes",
        ],
    ),
    # the preview mask must come from the same predicate export uses; a cache
    # that is never written shows a decal nowhere while the file has one
    # the facing gate is the one part of the original coverage rule that needs
    # nothing but the shape, and it is what keeps a decal off the far side
    "decal-facing": (
        "mapping/decals.py",
        "        if (normal_matrix @ polygon.normal).normalized().dot(axis) < min_cos:",
        "        if False:",
        ["test_a_decal_does_not_reach_the_far_side"],
    ),
    # the two import checkboxes.  Both trade fidelity for a workable scene, and
    # both are invisible until export, so each needs a test that notices
    "import-lod-skip": (
        "mapping/shape_to_blender.py",
        "    kept_slots = None if do_import_details else _slots_to_import(shape)",
        "    kept_slots = None",
        ["test_import_can_leave_the_lods_out"],
    ),
    "import-decal-meshes": (
        "mapping/shape_to_blender.py",
        "    if shape.decals and decals_as_meshes:",
        "    if False:",
        ["test_decals_can_import_as_meshes"],
    ),
    # a collision hull is not a level of detail; dropping it with the LODs
    # would quietly make a re-exported shape one the engine cannot collide with
    "import-keeps-collision": (
        "mapping/shape_to_blender.py",
        "        kept |= {(sub, d.object_detail_num) for d in mine if d.size < 0}",
        "        pass",
        ["test_collision_details_survive_leaving_the_lods_out"],
    ),
    # a decal's branch lives in a material and a material is shared, so without
    # the object gate the projector box draws the decal on every other mesh
    # standing in it -- which is 5999 of the corpus's 6053 decals
    "decal-object-gate": (
        "mapping/decals.py",
        "    host = decal_host_id(props.target) if props.target is not None else 0",
        "    host = 0",
        ["test_a_decal_previews_only_on_its_target"],
    ),
    # import fits the rule to each decal's own stored faces; without it the
    # default rule applies and round-trip coverage drops from 0.43 to 0.23
    "decal-fit": (
        "mapping/decals.py",
        "                projector.dts_decal.rule = rule",
        "                projector.dts_decal.rule = projector.dts_decal.rule",
        ["test_decal_coverage_recall_has_a_floor"],
    ),
    "decal-coverage-cache": (
        "mapping/decals.py",
        "        attr.data[polygon.index].value = 1.0 if polygon.index in wanted else 0.0",
        "        attr.data[polygon.index].value = 0.0",
        ["test_decal_coverage_is_what_the_export_writes"],
    ),
    "decal-projector-fit": (
        "mapping/decals.py",
        "    scale = 2.0 * half",
        "    scale = 0.5 * half",
        ["test_an_operator_decal_projects_inside_its_texture"],
    ),
    "mesh-flags": (
        "mapping/shape_to_blender.py",
        "def flags_from_blender(bobj, mesh_type: int) -> int:",
        "def flags_from_blender(bobj, mesh_type: int) -> int:\n    return 0",
        [
            "test_mesh_flags_survive_an_edit",
            "test_mesh_type_echo_bits_survive_an_edit",
            "test_a_billboard_can_be_authored_from_a_plain_mesh",
        ],
    ),
    "billboard-z": (
        "mapping/shape_to_blender.py",
        '    ("billboard_z", MESH_BILLBOARD_Z_AXIS),',
        "",
        ["test_a_billboard_can_be_authored_from_a_plain_mesh"],
    ),
    # the version gate on the ground-frame drop, broken both ways: not
    # dropping leaves the writer's refusal to cancel a v23 export, and
    # dropping unconditionally takes them off v24, where they fit.  Anchored
    # on `warnings = []` because the same `if` opens the writer's refusal
    # without the flush the export raises IndexError off an empty uv_layer.data
    "edit-mode-flush": (
        "mapping/blender_to_shape.py",
        '    if obj is None or obj.mode != "EDIT":',
        "    if True:",
        ["test_exporting_from_edit_mode"],
    ),
    # and the restore, which is the half a test could easily not check: the
    # mode is the user's and an export borrows it
    "edit-mode-restore": (
        "mapping/blender_to_shape.py",
        '        if context.view_layer.objects.active is obj:\n            bpy.ops.object.mode_set(mode="EDIT")',
        "        pass",
        ["test_exporting_from_edit_mode"],
    ),
    # the sort is the whole of the ordering rule, and its absence is invisible
    # to anything that only checks a shape exported: the file is valid, the
    # objects are all there, and only the draw order is wrong
    "translucent-last": (
        "mapping/blender_to_shape.py",
        "    order.sort(key=lambda k: k in translucent_keys)",
        "    pass",
        ["test_translucent_objects_are_written_last"],
    ),
    # the other half of the rule is what the sort must *not* do.  Only the
    # translucency may decide, so a tiebreaker added to the key -- the obvious
    # thing to reach for when a sort looks under-specified -- reshuffles the
    # opaque objects a shape was built in.  Inverting the key would not do:
    # with nothing translucent it is a constant either way, and a stable sort
    # leaves a constant key alone
    "translucent-last-stability": (
        "mapping/blender_to_shape.py",
        "    order.sort(key=lambda k: k in translucent_keys)",
        "    order.sort(key=lambda k: (k in translucent_keys, k))",
        ["test_an_all_opaque_shape_keeps_its_order"],
    ),
    "decals-need-translucency": (
        "mapping/blender_to_shape.py",
        "    if shape.decals and not shape_has_translucent_mesh(shape):",
        "    if False:",
        ["test_a_decal_needs_something_translucent_to_draw_against"],
    ),
    # a decal keeps its material on decal_data rather than in a primitive of
    # its own, so a check that reads primitives alone calls the 59 corpus
    # shapes whose only translucency is a decal's entirely opaque
    "decal-material-counts-as-translucent": (
        "dtslib/translucency.py",
        "    if mesh.decal_data is not None:\n        used.add(mesh.decal_data.material_index & PRIM_MATERIAL_MASK)",
        "    pass",
        ["test_a_translucent_decal_mesh_counts"],
    ),
    "ground-drop": (
        "dtslib/fit.py",
        "    if version in NO_GROUND_STORAGE and shape.ground_translations:",
        "    if False:",
        [
            "test_v23_drops_ground_frames_rather_than_refusing",
            "test_v23_drops_ground_frames",
        ],
    ),
    "ground-drop-version-gate": (
        "dtslib/fit.py",
        "    if version in NO_GROUND_STORAGE and shape.ground_translations:",
        "    if shape.ground_translations:",
        [
            "test_v24_keeps_the_ground_frames_v23_would_drop",
            "test_ground_frames_are_authorable",
        ],
    ),
    # --- the older output versions ----------------------------------------
    # Every one of these breaks a layout the *reader* is a port of, so the
    # damage shows up as a guard mismatch or a wrong value on the way back in --
    # never as an exception at write time, which is exactly why they need
    # mutating rather than eyeballing.
    "pair-node-tracks": (
        "dtslib/fit.py",
        "        pair_node_tracks(shape)",
        "        pass",
        ["test_pre_v22_pairs_a_translation_only_channel"],
    ),
    "keyframe-major-transpose": (
        "dtslib/old_writer.py",
        "    block = arr[start : start + total]",
        "    return",
        ["test_animation_survives_the_keyframe_major_versions"],
    ),
    "keyframe-table": (
        "dtslib/old_writer.py",
        "    if version < 17:\n        w.s32(len(keyframes))",
        "    if False:\n        w.s32(len(keyframes))",
        ["test_roundtrip"],
    ),
    "mesh-index-list": (
        "dtslib/old_writer.py",
        "            if mesh is None:\n                w.s32(-1)",
        "            if mesh is None:\n                w.s32(next_mesh)",
        ["test_roundtrip"],
    ),
    "encoded-normals-version-gate": (
        "dtslib/mesh_io.py",
        "        # compute (TSMesh::disassemble, tsMesh.cc:3168)\n        if version > 21:",
        "        # compute (TSMesh::disassemble, tsMesh.cc:3168)\n        if True:",
        ["test_roundtrip"],
    ),
    "pre-v20-decal-header": (
        "dtslib/mesh_io.py",
        "        alloc.set32(1)  # numFrames",
        "        pass  # numFrames",
        ["test_roundtrip"],
    ),
    "empty-skin-section": (
        "dtslib/writer.py",
        "        alloc.set32(0)  # numSkins",
        "        alloc.set32(1)  # numSkins",
        ["test_skins_survive_every_version"],
    ),
    "trim-matters-to-tables": (
        "dtslib/fit.py",
        "        trim_matters_to_tables(shape)",
        "        pass",
        ["test_roundtrip"],
    ),
    "matlist-reflection-gate": (
        "dtslib/matlist.py",
        "    if version > 20:\n        for m in mats:\n            w.f32(m.reflection_amount)",
        "    if False:\n        for m in mats:\n            w.f32(m.reflection_amount)",
        ["test_byte_identical"],
    ),
    "sequence-pre-v22-bools": (
        "dtslib/sequence_io.py",
        "    if version < 22:\n        w.u8(1 if seq.flags & SEQ_BLEND else 0)",
        "    if False:\n        w.u8(1 if seq.flags & SEQ_BLEND else 0)",
        ["test_roundtrip"],
    ),
    # A key added from Python arrives at 1.0, so leaving it there shows every
    # frame of the animation summed onto the rest pose.
    "frame-keys-rest-at-zero": (
        "mapping/shape_to_blender.py",
        "        sk.value = 0.0",
        "        pass",
        ["test_imported_frames_rest_at_the_first_frame"],
    ),
    # Object states are one block per object in the union of the matters sets;
    # indexing with a per-channel ordinal reads another object's block, and the
    # frame track arrives as zeroes.
    "object-state-union-ordinal": (
        "mapping/sequences.py",
        "        first = seq.base_object_state + membership.ordinal_of(obj_index) * n",
        "        first = seq.base_object_state + seq.frame_matters.ordinal_of(obj_index) * n",
        ["test_frame_track_previews_the_vertex_animation"],
    ),
    # The driver picks the key by its position among the frame keys; an offset
    # one out previews a neighbouring frame.
    "frame-driver-position": (
        "mapping/framepreview.py",
        'drv.expression = f"max(0.0, 1.0 - abs(frame - {position}))"',
        'drv.expression = f"max(0.0, 1.0 - abs(frame - {position + 1}))"',
        [
            "test_frame_track_previews_the_vertex_animation",
            "test_vertex_animation_previews_in_a_fresh_scene",
        ],
    ),
    # Wiring is what makes the track visible at all.
    "frame-preview-wiring": (
        "mapping/shape_to_blender.py",
        "            frame_wired = wire_frame_drivers(arm_obj, frame_names, warnings)",
        "            frame_wired = 0",
        ["test_frame_track_previews_the_vertex_animation"],
    ),
}

# the version mutations above are caught by the pytest fixture sweep, not by
# Blender: TestEveryVersion writes every fixture as all ten versions
RUNNERS.update(
    {
        "keyframe-table": "pytest",
        "mesh-index-list": "pytest",
        "encoded-normals-version-gate": "pytest",
        "pre-v20-decal-header": "pytest",
        "empty-skin-section": "pytest",
        "trim-matters-to-tables": "pytest",
        "matlist-reflection-gate": "pytest",
        "sequence-pre-v22-bools": "pytest",
    }
)


def _run_blender(work: Path, tests, blender: str) -> tuple[set, set]:
    proc = subprocess.run(
        [
            blender, "--background", "--factory-startup",
            "--python", str(work / "tests/blender/run_blender_tests.py"),
            "--", *tests,
        ],
        capture_output=True,
        text=True,
    )
    out = proc.stdout

    def named(prefix):
        # the runner labels results "module:test_name"; the module part is for
        # the reader, not for matching
        return {
            line.split()[1].rpartition(":")[2]
            for line in out.splitlines()
            if line.startswith(prefix)
        }

    return named("FAIL "), named("PASS ")


def _pytest_python() -> str:
    """The interpreter that actually has this project's pytest.

    Not `sys.executable`: this tool is documented as `scripts/mutate.py`, whose
    shebang is the system python, while the fast test loop lives in the
    checkout's .venv.  Getting it wrong fails silently in the worst direction --
    the subprocess dies on "No module named pytest", no FAILED lines are
    parsed, and the mutation reads as uncaught when it was never tried.
    """
    candidate = REPO / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def _run_pytest(work: Path, tests) -> tuple[set, set]:
    proc = subprocess.run(
        [_pytest_python(), "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
         "-m", "not corpus", *[f"-k={t}" for t in tests[:1]], "tests"],
        cwd=work,
        capture_output=True,
        text=True,
    )
    out = proc.stdout
    # Demand positive evidence that tests ran, rather than inferring it from
    # the absence of failures -- reporting "passed" for a run that never
    # happened is how a broken harness certifies itself healthy, which is the
    # one thing it must never do.  The exit code alone is not enough: a missing
    # pytest exits 1, the same as an honest test failure.  A summary line
    # naming passed/failed/error is the thing only a real run produces.
    ran = re.search(r"\b\d+ (passed|failed|error)", out)
    if proc.returncode not in (0, 1) or not ran:
        tail = (proc.stderr or out).strip().splitlines()
        print(f"       pytest exited {proc.returncode} without running: "
              f"{tail[-1] if tail else 'no output'}")
        return set(), set()

    failed = {t for t in tests if f"::{t}" in out and "FAILED" in out}
    # pytest names failures as path::Class::test, so match on the leaf
    for line in out.splitlines():
        if line.startswith("FAILED "):
            leaf = line.split()[1].split("::")[-1].split("[")[0]
            failed.add(leaf)
    passed = set() if failed else set(tests)
    return failed, passed


def run_mutation(name: str, blender: str) -> bool:
    path, find, replace, tests = MUTATIONS[name]
    runner = RUNNERS.get(name, "blender")
    with tempfile.TemporaryDirectory() as tmp:
        # the Blender runner imports the checkout by package name, so the copy
        # has to keep it
        work = Path(tmp) / "io_scene_dts"
        shutil.copytree(
            REPO, work, ignore=shutil.ignore_patterns(".git", "htmlcov", "dist", "__pycache__")
        )
        target = work / path
        source = target.read_text()
        if find not in source:
            print(f"  SKIP {name}: anchor no longer present in {path}")
            print("       (the code moved -- update the mutation, do not ignore it)")
            return False
        target.write_text(source.replace(find, replace, 1))

        if runner == "pytest":
            failed, passed = _run_pytest(work, tests)
        else:
            failed, passed = _run_blender(work, tests, blender)

    missing = [t for t in tests if t not in failed]
    if missing:
        print(f"  BAD  {name}: still passing -> {', '.join(missing)}")
        if any(t in passed for t in missing):
            print("       the test does not actually check what it claims to")
        else:
            print("       the test did not run at all")
        return False
    print(f"  ok   {name}: {', '.join(sorted(failed))} failed as intended")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="*", help="mutations to run (default: all)")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--blender", default="blender")
    args = parser.parse_args()

    if args.list:
        for name, (path, _, _, tests) in MUTATIONS.items():
            print(f"{name:20} {path:32} {', '.join(tests)}")
        return 0

    names = args.names or list(MUTATIONS)
    unknown = [n for n in names if n not in MUTATIONS]
    if unknown:
        print(f"unknown mutation(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    print(f"running {len(names)} mutation(s)")
    ok = [run_mutation(n, args.blender) for n in names]
    print(f"\n{sum(ok)}/{len(ok)} mutations were caught")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())

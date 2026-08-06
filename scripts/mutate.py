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
RUNNERS = {"sorted-threading": "pytest"}

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
        '        links.new(reflectance.outputs["Color"], bsdf.inputs["Metallic"])',
        "        pass",
        ["test_a_self_reflectance_imports_as_two_images"],
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
    "reflectance-forces-envmap": (
        "mapping/materials.py",
        "            mat.flags &= ~MAT_NEVER_ENV_MAP",
        "            pass",
        ["test_a_reflectance_material_is_env_mapped"],
    ),
    "reflectance-source-texture": (
        "mapping/texture_io.py",
        "        if resolved in sources:",
        "        if False:",
        ["test_export_does_not_overwrite_a_source_texture"],
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
}


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

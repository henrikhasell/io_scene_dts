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
    "decal-objects": (
        "mapping/blender_to_shape.py",
        '        if o.type != "MESH" or "dts_decal_name" in o:',
        '        if o.type != "MESH":',
        ["test_decal_meshes_are_not_exported_as_objects"],
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
    "sorted-mode": (
        "mapping/shape_to_blender.py",
        '    bobj["dts_sorted_mode"] = "BSP"',
        '    bobj["dts_sorted_mode"] = "NONE"',
        ["test_sorted_meshes_survive_an_edit"],
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
    "mesh-flags": (
        "mapping/shape_to_blender.py",
        "def flags_from_blender(bobj, mesh_type: int) -> int:",
        "def flags_from_blender(bobj, mesh_type: int) -> int:\n    return 0",
        ["test_mesh_flags_survive_an_edit", "test_mesh_type_echo_bits_survive_an_edit"],
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
    return (
        {line.split()[1] for line in out.splitlines() if line.startswith("FAIL ")},
        {line.split()[1] for line in out.splitlines() if line.startswith("PASS ")},
    )


def _run_pytest(work: Path, tests) -> tuple[set, set]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
         "-m", "not corpus", *[f"-k={t}" for t in tests[:1]], "tests"],
        cwd=work,
        capture_output=True,
        text=True,
    )
    out = proc.stdout
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

"""Sweep the v23 corpus through the Blender import -> export round-trip.

    blender --background --factory-startup --python tests/blender/roundtrip_sweep.py -- \
        [--only NAME ...] [--limit N] [--tol F] [--json PATH] [--roots t2|all] [-v]

Reports every structural difference per file rather than stopping at the first,
so the output is a taxonomy of what the Blender path actually loses.  Exits
non-zero if any file fails to import/export or shows differences.
"""

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests" / "blender"))

import harness  # noqa: E402

harness.add_repo_to_path()

import io_scene_dts  # noqa: E402
from dtslib import read_shape_file  # noqa: E402
from tests.corpus import T2_SHAPES, corpus_dts_files_of_version  # noqa: E402
from tests.util import compare_shapes  # noqa: E402


# Recomputed from the geometry on export rather than carried through Blender,
# so they are allowed to differ: the original tool derived them differently
# (it did not walk every detail level through the node transforms).  Preserving
# them instead would only let them go stale when geometry is edited.
TOLERATED_FIELDS = frozenset({"bounds", "center", "radius", "tube_radius"})


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="roundtrip_sweep")
    ap.add_argument("--only", nargs="*", default=None, help="file name substrings to include")
    ap.add_argument("--limit", type=int, default=None, help="stop after N files")
    ap.add_argument("--tol", type=float, default=0.0, help="float tolerance for geometry")
    ap.add_argument("--roots", choices=("t2", "all"), default="t2")
    ap.add_argument("--json", default=str(REPO / "dist" / "roundtrip_v23.json"))
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap.parse_args(argv)


def select(args):
    roots = () if args.roots == "all" else (T2_SHAPES,)
    files = corpus_dts_files_of_version(23, *roots)
    if args.only:
        files = [f for f in files if any(s in Path(f).name for s in args.only)]
    if args.limit:
        files = files[: args.limit]
    return files


def sweep_one(src, tol):
    """Round-trip one file; never raises."""
    rec = {"file": Path(src).name, "path": str(src), "status": "ok", "diffs": []}
    t0 = time.time()
    out = None
    try:
        harness.reset()
        harness.import_dts(src)
        out = harness.tmp_path(".dts")
        harness.export_dts(out, version="23")
        a, b = read_shape_file(str(src)), read_shape_file(out)
        rec["bytes_in"] = Path(src).stat().st_size
        rec["bytes_out"] = Path(out).stat().st_size
        diffs = compare_shapes(a, b, tol=tol, ignore_fields=TOLERATED_FIELDS)
        rec["diffs"] = [{"field": d.field, "detail": d.detail} for d in diffs]
        rec["status"] = "ok" if not diffs else "diff"
    except Exception as exc:
        rec["status"] = "error"
        rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["traceback"] = traceback.format_exc()
    finally:
        if out:
            Path(out).unlink(missing_ok=True)
        rec["seconds"] = round(time.time() - t0, 2)
    return rec


def summarize(records):
    """Collapse per-file diffs into a category x files taxonomy."""
    # meshes[N] collapses to "meshes[*].<fieldname>" so the table stays readable
    counts = {}
    for rec in records:
        seen = set()
        for d in rec["diffs"]:
            field = d["field"]
            if field.startswith("meshes["):
                for name in (d["detail"] or "?").split(", "):
                    seen.add(f"meshes[*].{name}")
            else:
                seen.add(field)
        for cat in seen:
            counts.setdefault(cat, []).append(rec["file"])
    return dict(sorted(counts.items(), key=lambda kv: (-len(kv[1]), kv[0])))


def main():
    args = parse_args()
    io_scene_dts.register()
    cov = harness.start_coverage("blender-sweep")

    files = select(args)
    print(f"v23 corpus: {len(files)} files (roots={args.roots}, tol={args.tol})")
    print(f"tolerated (recomputed on export): {', '.join(sorted(TOLERATED_FIELDS))}\n")

    records = []
    try:
        for i, src in enumerate(files, 1):
            rec = sweep_one(src, args.tol)
            records.append(rec)
            mark = {"ok": "OK  ", "diff": "DIFF", "error": "ERR "}[rec["status"]]
            extra = ""
            if rec["status"] == "diff":
                extra = f"  {len(rec['diffs'])} diffs"
            elif rec["status"] == "error":
                extra = f"  {rec['error']}"
            print(f"[{i:2}/{len(files)}] {mark} {rec['file']:<34} {rec['seconds']:6.1f}s{extra}")
            if args.verbose and rec["status"] == "diff":
                for d in rec["diffs"][:12]:
                    print(f"        - {d['field']}: {d['detail']}")
            sys.stdout.flush()
    finally:
        if cov is not None:
            cov.stop()
            cov.save()
        io_scene_dts.unregister()

    taxonomy = summarize(records)
    ok = sum(1 for r in records if r["status"] == "ok")
    diff = sum(1 for r in records if r["status"] == "diff")
    err = sum(1 for r in records if r["status"] == "error")

    print(f"\n{'=' * 72}\n{len(records)} files: {ok} clean, {diff} with differences, {err} errored\n")
    if taxonomy:
        print(f"{'category':<44} {'files':>5}")
        print("-" * 72)
        for cat, hit in taxonomy.items():
            print(f"{cat:<44} {len(hit):>5}")

    report = Path(args.json)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps(
            {
                "summary": {"total": len(records), "ok": ok, "diff": diff, "error": err},
                "taxonomy": {k: v for k, v in taxonomy.items()},
                "files": records,
            },
            indent=2,
        )
    )
    print(f"\nreport: {report}")
    sys.exit(0 if diff == 0 and err == 0 else 1)


main()

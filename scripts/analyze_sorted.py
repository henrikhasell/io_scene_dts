#!/usr/bin/env python3
"""Measure the sorted-mesh cluster tables in the on-disk corpus.

Answers the question the sorted-mesh rewrite hinges on: what does the shipped
art actually guarantee?  The add-on is dropping the pickled payload that
carried these tables verbatim, so it has to generate them -- and it can only be
held to a bar the original exporter itself clears.

Simulates the engine walk (tests/sorted_walk.py) from cameras spread over a
sphere and reports, per mesh, whether the walk terminates, whether it ever
draws a primitive twice, whether the set of triangles drawn depends on where
you stand, and how much of the mesh a walk covers.

Usage:
    scripts/analyze_sorted.py [--cameras 64] [--limit N] [--verbose]
"""

from __future__ import annotations

import argparse
import collections
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from dtslib import read_shape_file  # noqa: E402
from dtslib.types import SORTED_MESH  # noqa: E402
from tests.corpus import corpus_dts_files  # noqa: E402
from tests.sorted_walk import check_mesh  # noqa: E402


def sorted_meshes(paths):
    """(file, mesh index, mesh) for every sorted mesh, one file per basename.

    The corpus roots overlap -- the same shape ships in more than one place --
    and counting a mesh twice would skew every ratio below.
    """
    seen = set()
    for path in paths:
        base = os.path.basename(path)
        if base in seen:
            continue
        seen.add(base)
        try:
            shape = read_shape_file(path)
        except Exception:
            continue
        for i, mesh in enumerate(shape.meshes):
            if mesh is not None and mesh.mesh_type == SORTED_MESH and mesh.sorted_data:
                yield base, i, mesh


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cameras", type=int, default=64)
    parser.add_argument("--limit", type=int, default=None, help="stop after N meshes")
    parser.add_argument("--verbose", action="store_true", help="list every imperfect mesh")
    args = parser.parse_args()

    paths = corpus_dts_files()
    if not paths:
        print("no corpus on this machine; nothing to analyze")
        return 0

    tally = collections.Counter()
    shapes = set()
    coverage = []
    ordering = []
    imperfect = []
    total = 0

    for base, index, mesh in sorted_meshes(paths):
        if args.limit is not None and total >= args.limit:
            break
        total += 1
        shapes.add(base)
        result = check_mesh(mesh, args.cameras)
        for key in ("terminates", "no_repeat", "same_set", "complete"):
            tally[key] += bool(result[key])
        coverage.append(result["coverage"])
        # a mesh whose every walk draws one primitive has no pair to score
        if result["pairs"]:
            ordering.append(result["ordering"])
        if not (result["same_set"] and result["complete"]):
            imperfect.append((base, index, result))

    print(f"{total} sorted meshes across {len(shapes)} distinct shapes, "
          f"{args.cameras} cameras each\n")
    for key, label in (
        ("terminates", "walk terminates"),
        ("no_repeat", "no primitive drawn twice"),
        ("same_set", "triangle set is camera-independent"),
        ("complete", "  ...and covers the whole mesh"),
    ):
        print(f"  {label:38} {tally[key]:4d}/{total}")

    coverage.sort()
    ordering.sort()

    def pct(values, q):
        return values[min(len(values) - 1, int(q * len(values)))]

    print(f"\n  worst-camera triangle coverage   min {min(coverage):.3f}  "
          f"p05 {pct(coverage, 0.05):.3f}  median {pct(coverage, 0.5):.3f}")
    print(f"  back-to-front ordering           min {min(ordering):.3f}  "
          f"p05 {pct(ordering, 0.05):.3f}  median {pct(ordering, 0.5):.3f}"
          f"   ({len(ordering)} meshes scored)")

    if imperfect:
        print(f"\n{len(imperfect)} mesh(es) draw a camera-dependent triangle set.")
        print("This is the bar a generated tree has to clear, not a bug to fix:")
        for base, index, result in imperfect[: (None if args.verbose else 10)]:
            print(f"  {base}[{index}]  coverage {result['coverage']:.3f}  "
                  f"ordering {result['ordering']:.3f}")
        if not args.verbose and len(imperfect) > 10:
            print(f"  ... and {len(imperfect) - 10} more (--verbose for all)")

    print("\nVerdict")
    if tally["terminates"] == total and tally["no_repeat"] == total:
        print("  Structure: the linked-cluster reading holds. Walks terminate and")
        print("  leaf ranges partition the primitive list, so a generated tree can")
        print("  use the same emit(node, continuation) threading.")
    else:
        print("  Structure: the linked-cluster reading is WRONG. Ship FLAT mode only.")
    print("  Ordering: centroid distance is a proxy for what a BSP guarantees, and")
    print(f"  the shipped art scores a median of {pct(ordering, 0.5):.3f} on it. Treat that")
    print("  as the bar for a generated tree, not 1.0 -- and note that neither")
    print("  number can be validated against the real renderer outside the game.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

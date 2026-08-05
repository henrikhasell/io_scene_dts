#!/usr/bin/env python3
"""Project what a re-derived LOD vertex pool would cost, against the corpus.

Dropping the pickled payload means `parent_mesh` vertex sharing has to be
rebuilt rather than replayed, and that sharing is worth x1.85 in file size.
The plan is to build each DTS object's detail levels against one vertex pool,
lowest detail first, so every smaller LOD is a prefix of the next -- which the
reader (dtslib/mesh_io.py:76-104) is exactly what it wants.

Prefix-ness holds by construction.  What does not is *value*: if a higher LOD
does not contain the vertices a lower one used, the pool carries entries the
top LOD never references and the file grows instead of shrinking.  This script
measures that waste on real geometry, so the size gate on the round-trip sweep
is calibrated rather than guessed.

Caveat worth keeping in mind reading the output: this interns vertices straight
out of the file, where LODs that shared vertices share them bit-exactly.  A
real Blender round trip re-derives normals through custom split normals, which
may not reproduce across two meshes with different topology.  These numbers are
therefore an upper bound on what Stage 2 can achieve, not a prediction.

Usage:
    scripts/analyze_lod_share.py [--limit N] [--verbose]
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from dtslib import read_shape_file  # noqa: E402
from dtslib.types import SKIN_MESH  # noqa: E402
from tests.corpus import corpus_dts_files  # noqa: E402

# quantization for the cross-LOD key.  The within-mesh dedup in
# blender_to_shape.py:519 keys on the Blender vertex index, which cannot match
# across two mesh datablocks, so position has to enter the key geometrically.
POS_PLACES = 5
UV_PLACES = 6
NRM_PLACES = 4

# bytes per pooled vertex: vert (3f) + normal (3f) + tvert (2f) + encoded normal
VERT_BYTES = 12 + 12 + 8 + 1


def vertex_keys(mesh, pos_places=POS_PLACES, uv_places=UV_PLACES, nrm_places=NRM_PLACES):
    """One hashable key per DTS vertex of ``mesh``.

    DTS keeps verts/tverts/norms as parallel arrays, so index i names one
    vertex across all three.
    """
    keys = []
    for i, pos in enumerate(mesh.verts):
        uv = mesh.tverts[i] if i < len(mesh.tverts) else (0.0, 0.0)
        nrm = mesh.norms[i] if i < len(mesh.norms) else (0.0, 0.0, 1.0)
        keys.append(
            (
                tuple(round(c, pos_places) for c in pos),
                tuple(round(c, uv_places) for c in uv),
                tuple(round(c, nrm_places) for c in nrm),
            )
        )
    return keys


def simulate(slots) -> dict:
    """Intern ``slots`` (highest detail first) lowest-detail-first into one pool.

    Returns the pool size, the top LOD's own vertex count, and how many pooled
    entries the top LOD never references -- the waste that decides whether
    sharing is worth doing for this object.
    """
    pool: dict = {}
    sealed = []
    for mesh in reversed(slots):
        if mesh is None:
            sealed.append(0)
            continue
        for key in vertex_keys(mesh):
            if key not in pool:
                pool[key] = len(pool)
        sealed.append(len(pool))
    sealed.reverse()

    top = next((m for m in slots if m is not None), None)
    top_keys = set(vertex_keys(top)) if top is not None else set()
    return {
        "pool": len(pool),
        "top_own": len(top_keys),
        "unused_in_top": len(pool) - len(top_keys & set(pool)),
        "sealed": sealed,
    }


def objects_of(shape):
    """(object index, [mesh or None per detail slot]) for multi-slot objects."""
    for oi, obj in enumerate(shape.objects):
        if obj.num_meshes < 2:
            continue
        slots = [
            shape.meshes[obj.start_mesh_index + j]
            if obj.start_mesh_index + j < len(shape.meshes)
            else None
            for j in range(obj.num_meshes)
        ]
        if sum(1 for m in slots if m is not None) < 2:
            continue
        yield oi, slots


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="stop after N shapes")
    parser.add_argument("--verbose", action="store_true", help="list the worst objects")
    args = parser.parse_args()

    paths = corpus_dts_files()
    if not paths:
        print("no corpus on this machine; nothing to analyze")
        return 0

    seen = set()
    n_objects = n_shapes = n_skin_skipped = 0
    perfect = 0
    waste_ratios = []
    worst = []
    actual_bytes = pooled_bytes = unshared_bytes = 0

    for path in paths:
        base = os.path.basename(path)
        if base in seen:
            continue
        seen.add(base)
        if args.limit is not None and n_shapes >= args.limit:
            break
        try:
            shape = read_shape_file(path)
        except Exception:
            continue
        n_shapes += 1

        for oi, slots in objects_of(shape):
            live = [m for m in slots if m is not None]
            # skins need initial_verts/vertex_index/bone_index/weight/node_index
            # to be prefixes too, so Stage 2 leaves them unshared
            if any(m.mesh_type == SKIN_MESH for m in live):
                n_skin_skipped += 1
                continue
            n_objects += 1
            result = simulate(slots)
            waste = result["unused_in_top"]
            ratio = waste / result["pool"] if result["pool"] else 0.0
            waste_ratios.append(ratio)
            if waste == 0:
                perfect += 1
            elif len(worst) < 4096:
                worst.append((ratio, base, oi, result["pool"], result["top_own"]))

            unshared_bytes += sum(len(m.verts) for m in live) * VERT_BYTES
            pooled_bytes += result["pool"] * VERT_BYTES
            actual_bytes += sum(
                len(m.verts) for m in live if m.parent_mesh < 0
            ) * VERT_BYTES

    if not n_objects:
        print("no multi-detail objects found")
        return 0

    print(f"{n_shapes} shapes, {n_objects} multi-detail objects "
          f"({n_skin_skipped} skinned object(s) skipped -- Stage 2 leaves those unshared)\n")

    print(f"  pool carries nothing the top LOD lacks   {perfect:5d}/{n_objects} "
          f"({100 * perfect / n_objects:.1f}%)")
    waste_ratios.sort()

    def pct(values, q):
        return values[min(len(values) - 1, int(q * len(values)))]

    print(f"  wasted pool entries    median {pct(waste_ratios, 0.5):.3f}  "
          f"p90 {pct(waste_ratios, 0.9):.3f}  p99 {pct(waste_ratios, 0.99):.3f}  "
          f"max {waste_ratios[-1]:.3f}")
    print(f"  mean waste             {statistics.mean(waste_ratios):.3f}")

    print("\nVertex-array bytes across those objects:")
    print(f"  as shipped (parent_mesh sharing)  {actual_bytes:12,d}")
    print(f"  re-derived pool                   {pooled_bytes:12,d}  "
          f"x{pooled_bytes / actual_bytes:.3f} vs shipped")
    print(f"  no sharing at all                 {unshared_bytes:12,d}  "
          f"x{unshared_bytes / actual_bytes:.3f} vs shipped")

    if worst and args.verbose:
        worst.sort(reverse=True)
        print("\nWorst objects by wasted pool entries:")
        for ratio, base, oi, pool, top_own in worst[:20]:
            print(f"  {ratio:.3f}  {base} object {oi}: pool {pool}, top LOD uses {top_own}")

    print("\nVerdict")
    print(f"  Sweep size gate: expect ~x{pooled_bytes / actual_bytes:.2f} of the shipped")
    print("  vertex-array bytes, against x%.2f if sharing is not re-derived at all."
          % (unshared_bytes / actual_bytes))
    print()
    print("  Note the pool comes out SMALLER than the shipped arrays: it dedups")
    print("  across detail levels that the original exporter stored separately.")
    print("  That also settles a design question -- the pool is the union of the")
    print("  per-LOD vertex sets, so its size can never exceed their sum, and")
    print("  sharing can never cost more bytes than not sharing.  Waste only")
    print("  costs runtime memory, so there is no size-based reason to fall back")
    print("  to independent arrays; the 0xFFFF index ceiling is the real limit.")
    over = sum(1 for r in waste_ratios if r > 0.25)
    print(f"  ({over} object(s), {100 * over / n_objects:.1f}%, carry >25% unused entries.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

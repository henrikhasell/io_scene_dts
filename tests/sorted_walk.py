"""A simulation of the engine's sorted-mesh cluster walk.

Like ``tests/corpus.py`` this imports neither ``pytest`` nor ``bpy``: it is
shared between ``scripts/analyze_sorted.py`` (which measures the shipped art)
and the unit tests for the cluster builder (which assert the same properties of
what we generate).  Keeping one implementation means the builder is checked
against the same reading of the format the analysis was based on.

The walk is TSSortedMesh::render (tsSortedMesh.cc): start at
``start_cluster[frame]``, draw the cluster's ``[start, end)`` primitive range,
then descend to ``front`` when the camera is in front of the cluster's plane
and ``back`` otherwise, until the link is -1.  A cluster with an empty range is
a pure decision node.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dtslib.primitives import bits_to_f32  # noqa: E402

# Clusters are 8 words: start/end primitive, plane normal xyz, plane k, front
# cluster, back cluster (dtslib/mesh_io.py:167-171).  The reader takes all
# eight with get32n and the writer puts them back with set32n, so the four
# plane words are float *bit patterns* sitting in an int table -- reading them
# as though they were already floats gives a plane test that is pure noise.
CLUSTER_WORDS = 8


def plane_of(cluster) -> tuple[float, float, float, float]:
    """The cluster's splitting plane, bit-cast back out of the word table."""
    return tuple(bits_to_f32(w) for w in cluster[2:6])


class WalkError(Exception):
    """The walk did not terminate, or left the cluster table."""


def walk(sorted_data, camera, frame: int = 0) -> list[int]:
    """Primitive indices drawn, in draw order, for a camera at ``camera``.

    ``camera`` is a point in the mesh's own space, not a direction: the plane
    test is ``n . p + k > 0``, so an offset plane needs a position to be
    meaningful.
    """
    if not sorted_data.start_cluster:
        raise WalkError("no start cluster")
    clusters = sorted_data.clusters
    out: list[int] = []
    cluster = sorted_data.start_cluster[frame]
    # every step consumes a link, so a walk longer than the table has a cycle
    for _ in range(len(clusters) + 1):
        if cluster < 0:
            return out
        if cluster >= len(clusters):
            raise WalkError(f"cluster {cluster} out of range ({len(clusters)})")
        start, end, _, _, _, _, front, back = clusters[cluster]
        nx, ny, nz, k = plane_of(clusters[cluster])
        out.extend(range(start, end))
        cluster = front if (nx * camera[0] + ny * camera[1] + nz * camera[2] + k) > 0 else back
    raise WalkError(f"walk did not terminate within {len(clusters)} steps")


def sphere_points(count: int, centre, radius: float) -> list[tuple[float, float, float]]:
    """``count`` camera positions spread evenly over a sphere.

    A deterministic Fibonacci spiral rather than random sampling, so a failure
    reproduces exactly and two runs are comparable.
    """
    points = []
    golden = math.pi * (3.0 - math.sqrt(5.0))
    for i in range(count):
        z = 1.0 - (2.0 * i + 1.0) / count
        r = math.sqrt(max(0.0, 1.0 - z * z))
        theta = golden * i
        points.append(
            (
                centre[0] + radius * r * math.cos(theta),
                centre[1] + radius * r * math.sin(theta),
                centre[2] + radius * z,
            )
        )
    return points


def camera_positions(verts, count: int = 64) -> list[tuple[float, float, float]]:
    """Cameras on a sphere enclosing ``verts`` at three times its radius."""
    if not verts:
        return sphere_points(count, (0.0, 0.0, 0.0), 1.0)
    centre = tuple(sum(v[i] for v in verts) / len(verts) for i in range(3))
    radius = max(math.dist(centre, v) for v in verts) or 1.0
    return sphere_points(count, centre, 3.0 * radius)


def triangles_of(primitives, indices, which) -> list[tuple[int, int, int]]:
    """Triangles of the given primitive indices, as drawn.

    Sorted meshes are indexed triangle lists throughout the corpus, so this
    deliberately does not decode strips -- a strip would make "the order
    triangles are drawn in" ambiguous, and the builder never emits one.
    """
    tris = []
    for pi in which:
        prim = primitives[pi]
        idx = indices[prim.start : prim.start + prim.num_elements]
        for i in range(0, len(idx) - 2, 3):
            tris.append((idx[i], idx[i + 1], idx[i + 2]))
    return tris


def _key(tri) -> tuple:
    return tuple(sorted(tri))


def primitive_centroid(verts, primitives, indices, pi):
    prim = primitives[pi]
    used = set(indices[prim.start : prim.start + prim.num_elements])
    if not used:
        return (0.0, 0.0, 0.0)
    return tuple(sum(verts[i][axis] for i in used) / len(used) for axis in range(3))


def ordering_score(verts, primitives, indices, drawn, camera) -> tuple[int, int]:
    """(adjacent primitive pairs drawn back to front, total adjacent pairs).

    Measured over *primitives*, not triangles: a primitive is the unit the
    cluster table orders, and triangles within one are in arbitrary order, so
    scoring them individually just measures noise.

    Centroid distance is only a proxy for what a BSP actually guarantees --
    correctness relative to the splitting planes -- so treat this as a
    comparison against the shipped art rather than an absolute.
    """
    depths = [math.dist(primitive_centroid(verts, primitives, indices, pi), camera) for pi in drawn]
    good = sum(1 for i in range(len(depths) - 1) if depths[i] >= depths[i + 1] - 1e-6)
    return good, max(0, len(depths) - 1)


def check_mesh(mesh, count: int = 64) -> dict:
    """Walk one sorted mesh from ``count`` cameras and summarise.

    Keys: terminates, no_repeat (no primitive drawn twice in one walk),
    same_set (the drawn triangle set does not depend on the camera), complete
    (that set is every triangle the mesh holds), coverage (worst-case fraction
    of the mesh's triangles drawn), ordering (fraction of adjacent primitive
    pairs drawn back to front), pairs (how many pairs that ratio is over -- 0
    means every walk drew a single primitive and the ratio is meaningless).
    """
    sd = mesh.sorted_data
    result = {
        "terminates": True,
        "no_repeat": True,
        "same_set": True,
        "complete": True,
        "coverage": 1.0,
        "ordering": 1.0,
        "pairs": 0,
        "error": None,
    }
    every = {_key(t) for t in triangles_of(mesh.primitives, mesh.indices, range(len(mesh.primitives)))}
    seen_sets = set()
    worst_coverage = 1.0
    good = total = 0
    for camera in camera_positions(mesh.verts, count):
        try:
            drawn = walk(sd, camera)
        except WalkError as exc:
            result["terminates"] = False
            result["error"] = str(exc)
            return result
        if len(set(drawn)) != len(drawn):
            result["no_repeat"] = False
        keys = frozenset(_key(t) for t in triangles_of(mesh.primitives, mesh.indices, drawn))
        seen_sets.add(keys)
        if every:
            worst_coverage = min(worst_coverage, len(keys & every) / len(every))
        g, t = ordering_score(mesh.verts, mesh.primitives, mesh.indices, drawn, camera)
        good += g
        total += t
    result["same_set"] = len(seen_sets) == 1
    result["complete"] = seen_sets == {frozenset(every)}
    result["coverage"] = worst_coverage
    result["ordering"] = (good / total) if total else 1.0
    result["pairs"] = total
    return result

"""Build a TSSortedMesh cluster tree from plain geometry.

A sorted mesh is the engine's answer to translucency.  Its triangles are
partitioned at export time into a small tree of *clusters*, each carrying a
splitting plane and two links; at draw time ``TSSortedMesh::render`` starts at
``start_cluster``, draws the cluster's primitive range, then follows ``front``
or ``back`` depending on which side of the plane the camera is on, until a link
is -1.  The result is a back-to-front draw order computed by a handful of dot
products instead of a per-frame sort.

Every sorted mesh in the Tribes 2 corpus sits on a translucent material --
trees, vehicle canopies -- with 6 to 24 clusters.

The tables used to survive only inside a pickled payload, which meant a sorted
mesh could be looked at and not touched.  This regenerates them, so the mesh is
ordinary editable geometry like any other.

Two things about the shape of the output, both read off the shipped art by
``scripts/analyze_sorted.py``:

- **Leaf ranges partition the primitive list exactly** -- no primitive appears
  in two clusters -- while the *geometry* is duplicated.  That is because each
  subtree is emitted once per traversal context: a walk that arrives with the
  camera in front needs the back half drawn first, and a walk arriving from the
  other side needs the reverse, so both orders are laid down and the links pick
  one.  Cost is ``2 ** depth`` copies of the index buffer, which is why depth
  is small and adjustable.
- **A generated tree only has to be as good as the original.**  Across 119
  shipped sorted meshes the drawn triangle set is camera-independent in just
  65, worst-camera coverage bottoms out at 0.730, and back-to-front ordering
  scores a median of 0.576.  Not splitting triangles -- which would change the
  vertex count and break the detail-level vertex sharing -- means a quad
  crossing a splitting plane sometimes draws out of order, so perfection is not
  on the table either way.
"""

from __future__ import annotations

import math

from .primitives import f32_to_bits
from .types import PRIM_INDEXED, PRIM_TRIANGLES, Primitive, SortedData

# a vertex is on the plane if it is within this of it, in model units
ON_PLANE = 1e-5
# how many candidate planes to score when splitting a group
CANDIDATES = 32


def _cluster(start, end, plane, front, back):
    """One 8-word cluster record.

    The plane rides in the table as raw dwords: the reader takes all eight with
    get32n and the writer puts them back with set32n (dtslib/mesh_io.py:165,
    :307), so the float bits are carried rather than converted.  Anything built
    here has to enter that representation too, or the writer is handed a float
    where it wants an int.
    """
    return (
        start,
        end,
        f32_to_bits(plane[0]),
        f32_to_bits(plane[1]),
        f32_to_bits(plane[2]),
        f32_to_bits(plane[3]),
        front,
        back,
    )


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _plane_of(verts, tri):
    """(nx, ny, nz, k) for a triangle's own plane, or None if degenerate."""
    a, b, c = verts[tri[0]], verts[tri[1]], verts[tri[2]]
    n = _cross(_sub(b, a), _sub(c, a))
    length = math.sqrt(_dot(n, n))
    if length < 1e-12:
        return None
    n = (n[0] / length, n[1] / length, n[2] / length)
    return (n[0], n[1], n[2], -_dot(n, a))


def _side(verts, tri, plane):
    """+1 in front, -1 behind, 0 straddling or coplanar."""
    nx, ny, nz, k = plane
    front = back = False
    for i in tri[:3]:
        v = verts[i]
        d = nx * v[0] + ny * v[1] + nz * v[2] + k
        if d > ON_PLANE:
            front = True
        elif d < -ON_PLANE:
            back = True
    if front and back:
        return 0
    return 1 if front else (-1 if back else 0)


def _centroid_side(verts, tri, plane):
    nx, ny, nz, k = plane
    cx = sum(verts[i][0] for i in tri[:3]) / 3.0
    cy = sum(verts[i][1] for i in tri[:3]) / 3.0
    cz = sum(verts[i][2] for i in tri[:3]) / 3.0
    return 1 if nx * cx + ny * cy + nz * cz + k >= 0.0 else -1


def _partition(verts, group, plane):
    """Split a group in two.  Straddling triangles go whole to the side their
    centroid falls on -- splitting one would add vertices, which would change
    the mesh's vertex count and break detail-level vertex sharing."""
    front, back = [], []
    for tri in group:
        side = _side(verts, tri, plane)
        if side == 0:
            side = _centroid_side(verts, tri, plane)
        (front if side > 0 else back).append(tri)
    return front, back


def _choose_plane(verts, group):
    """The candidate face plane that splits ``group`` most evenly.

    Candidates are sampled at a fixed stride rather than randomly, so the same
    geometry always produces the same tree and a bad tree can be reproduced.
    """
    step = max(1, len(group) // CANDIDATES)
    best, best_score = None, None
    for tri in group[::step]:
        plane = _plane_of(verts, tri)
        if plane is None:
            continue
        front = back = straddle = 0
        for other in group:
            side = _side(verts, other, plane)
            if side > 0:
                front += 1
            elif side < 0:
                back += 1
            else:
                straddle += 1
        if front == 0 or back == 0:
            continue  # no split at all
        score = abs(front - back) + 8 * straddle
        if best_score is None or score < best_score:
            best, best_score = plane, score
    return best


def build_sorted(
    verts,
    tris,
    *,
    depth: int = 2,
    leaf_size: int = 16,
    num_frames: int = 1,
    num_mat_frames: int = 1,
    always_write_depth: int = 0,
):
    """Partition ``tris`` into a threaded cluster tree.

    ``tris`` is a list of ``(a, b, c, material_word)`` in DTS winding.  Returns
    ``(primitives, indices, sorted_data)``; cluster ranges name contiguous
    ``[start, end)`` slices of ``primitives``, which is why the tree is built
    and the primitives emitted in one pass rather than two.
    """
    primitives: list[Primitive] = []
    indices: list[int] = []
    clusters: list[tuple] = []

    def emit_leaf(group, continuation):
        start = len(primitives)
        by_material: dict[int, list[int]] = {}
        for a, b, c, material in group:
            by_material.setdefault(material, []).extend((a, b, c))
        for material, idx in by_material.items():
            primitives.append(Primitive(len(indices), len(idx), material))
            indices.extend(idx)
        # a leaf decides nothing, so both links go to the continuation; the
        # plane is the constant 1.0, which always takes `front`
        clusters.append(_cluster(start, len(primitives), (0.0, 0.0, 0.0, 1.0),
                                 continuation, continuation))
        return len(clusters) - 1

    def emit(group, continuation, remaining):
        if not group:
            return continuation
        if remaining <= 0 or len(group) <= leaf_size:
            return emit_leaf(group, continuation)
        plane = _choose_plane(verts, group)
        if plane is None:
            return emit_leaf(group, continuation)
        front, back = _partition(verts, group, plane)
        if not front or not back:
            return emit_leaf(group, continuation)

        # With the camera in front of the plane, what is behind it has to be
        # drawn first; arriving from the other side wants the opposite.  Each
        # subtree is therefore laid down twice, once per arrival order, and the
        # links choose between them.
        front_entry = emit(back, emit(front, continuation, remaining - 1), remaining - 1)
        back_entry = emit(front, emit(back, continuation, remaining - 1), remaining - 1)
        at = len(primitives)
        clusters.append(_cluster(at, at, plane, front_entry, back_entry))
        return len(clusters) - 1

    root = emit(list(tris), -1, depth)

    data = SortedData()
    data.clusters = clusters
    data.start_cluster = [root] * max(1, num_frames)
    data.first_verts = [0] * max(1, num_frames)
    # a hint for glLockArraysEXT rather than a bound; the shipped files often
    # overstate it, and the true count is always right
    data.num_verts = [len(verts)] * max(1, num_frames)
    data.first_tverts = [0] * max(1, num_mat_frames)
    data.always_write_depth = always_write_depth
    return primitives, indices, data


def flat_sorted(
    primitives,
    verts,
    *,
    num_frames: int = 1,
    num_mat_frames: int = 1,
    always_write_depth: int = 0,
) -> SortedData:
    """One cluster covering every primitive, drawn in array order.

    The safe harbour: it keeps the mesh type and its flags word without
    claiming an ordering the geometry was never partitioned for.
    """
    data = SortedData()
    data.clusters = [_cluster(0, len(primitives), (0.0, 0.0, 0.0, 1.0), -1, -1)]
    data.start_cluster = [0] * max(1, num_frames)
    data.first_verts = [0] * max(1, num_frames)
    data.num_verts = [len(verts)] * max(1, num_frames)
    data.first_tverts = [0] * max(1, num_mat_frames)
    data.always_write_depth = always_write_depth
    return data


def triangles_of(mesh) -> list[tuple[int, int, int, int]]:
    """(a, b, c, material word) for an indexed-triangle mesh.

    Sorted meshes are indexed triangle lists throughout the corpus and the
    exporter only ever emits those, so this deliberately does not decode
    strips.
    """
    out = []
    for prim in mesh.primitives:
        idx = mesh.indices[prim.start : prim.start + prim.num_elements]
        for i in range(0, len(idx) - 2, 3):
            out.append((idx[i], idx[i + 1], idx[i + 2], prim.mat_index))
    return out


def material_word(mat_index: int) -> int:
    return PRIM_TRIANGLES | PRIM_INDEXED | (mat_index & 0x0FFFFFFF)

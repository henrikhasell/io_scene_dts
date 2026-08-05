"""The sorted-mesh cluster builder (dtslib/sorted_build.py).

Checked with tests/sorted_walk.py -- the same simulation of the engine's walk
that measured the shipped art in scripts/analyze_sorted.py, so the generated
trees are held to the format as it was actually read, not to a second reading
of it invented here.

The bar those measurements set, over 119 shipped sorted meshes: walks always
terminate and never draw a primitive twice, but the drawn triangle set is
camera-independent in only 65 of them, and back-to-front ordering scores a
median of 0.576.  A generated tree is expected to beat that, not to be perfect.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dtslib.sorted_build import build_sorted, flat_sorted, material_word  # noqa: E402
from tests.sorted_walk import camera_positions, triangles_of, walk  # noqa: E402


class _Mesh:
    """Just enough of dtslib.Mesh for the walk simulator."""

    def __init__(self, verts, primitives, indices, sorted_data):
        self.verts = verts
        self.primitives = primitives
        self.indices = indices
        self.sorted_data = sorted_data


def grid(nx=6, ny=6, z=0.0):
    """A flat grid of quads: every triangle shares one plane, so nothing can
    split it.  The degenerate case, not the representative one."""
    verts, tris = [], []
    for y in range(ny + 1):
        for x in range(nx + 1):
            verts.append((float(x), float(y), z))
    w = nx + 1
    for y in range(ny):
        for x in range(nx):
            a, b, c, d = y * w + x, y * w + x + 1, (y + 1) * w + x + 1, (y + 1) * w + x
            tris.append((a, b, c, material_word(0)))
            tris.append((a, c, d, material_word(0)))
    return verts, tris


def cards(n=24):
    """Foliage cards: quads at varied angles and offsets.

    Unlike grid(), these do not share a plane, so the builder can actually
    split them -- which is the case sorted meshes exist for.
    """
    verts, tris = [], []
    for i in range(n):
        angle = i * (math.pi / n) * 2.7
        cx = (i % 5) * 2.0
        cy = ((i // 5) % 5) * 2.0
        cz = (i % 3) * 1.5
        dx, dy = math.cos(angle), math.sin(angle)
        base = len(verts)
        for sx, sz in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            verts.append((cx + dx * sx, cy + dy * sx, cz + sz))
        tris.append((base, base + 1, base + 2, material_word(i % 2)))
        tris.append((base, base + 2, base + 3, material_word(i % 2)))
    return verts, tris


def cloud(n=80):
    """Scattered triangles, deterministic without random()."""
    verts, tris = [], []
    for i in range(n):
        # a cheap deterministic scatter: irrational strides fill the cube
        base = len(verts)
        for j in range(3):
            t = (i * 3 + j) + 1
            verts.append(
                (
                    (t * 0.6180339887) % 1.0 * 10.0,
                    (t * 0.4142135624) % 1.0 * 10.0,
                    (t * 0.7320508076) % 1.0 * 10.0,
                )
            )
        tris.append((base, base + 1, base + 2, material_word(i % 3)))
    return verts, tris


def build(verts, tris, **kw):
    prims, idx, data = build_sorted(verts, tris, **kw)
    return _Mesh(verts, prims, idx, data)


class TestStructure:
    def test_every_walk_terminates(self):
        mesh = build(*grid())
        for camera in camera_positions(mesh.verts, 64):
            walk(mesh.sorted_data, camera)  # raises WalkError if it does not

    def test_no_primitive_is_drawn_twice(self):
        mesh = build(*cloud())
        for camera in camera_positions(mesh.verts, 32):
            drawn = walk(mesh.sorted_data, camera)
            assert len(set(drawn)) == len(drawn)

    def test_leaf_ranges_partition_the_primitive_list(self):
        """The shipped files have this property exactly; so must ours."""
        mesh = build(*cloud())
        covered = []
        for start, end, *_ in mesh.sorted_data.clusters:
            covered.extend(range(start, end))
        assert sorted(covered) == list(range(len(mesh.primitives)))

    def test_cluster_links_stay_in_range(self):
        mesh = build(*cloud())
        n = len(mesh.sorted_data.clusters)
        for *_, front, back in mesh.sorted_data.clusters:
            assert -1 <= front < n
            assert -1 <= back < n

    def test_tables_are_sized_for_their_frames(self):
        verts, tris = grid()
        _, _, data = build_sorted(verts, tris, num_frames=3, num_mat_frames=5)
        assert len(data.start_cluster) == 3
        assert len(data.first_verts) == 3
        assert len(data.num_verts) == 3
        assert len(data.first_tverts) == 5
        assert data.num_verts[0] == len(verts)


class TestCoverage:
    def test_every_triangle_is_drawn_from_every_camera(self):
        """Stronger than the shipped art manages: 54 of 119 corpus meshes drop
        triangles depending on where you stand."""
        mesh = build(*grid())
        every = {tuple(sorted(t)) for t in triangles_of(mesh.primitives, mesh.indices,
                                                        range(len(mesh.primitives)))}
        for camera in camera_positions(mesh.verts, 64):
            drawn = {
                tuple(sorted(t))
                for t in triangles_of(mesh.primitives, mesh.indices, walk(mesh.sorted_data, camera))
            }
            assert drawn == every

    def test_the_scattered_case_is_covered_too(self):
        mesh = build(*cloud())
        every = {tuple(sorted(t)) for t in triangles_of(mesh.primitives, mesh.indices,
                                                        range(len(mesh.primitives)))}
        for camera in camera_positions(mesh.verts, 32):
            drawn = {
                tuple(sorted(t))
                for t in triangles_of(mesh.primitives, mesh.indices, walk(mesh.sorted_data, camera))
            }
            assert drawn == every

    def test_geometry_is_duplicated_once_per_traversal_context(self):
        """2**depth copies -- the price of a threaded tree with no per-frame
        sort, and the reason depth is a knob."""
        verts, tris = cards()
        for depth in (0, 1, 2):
            _, indices, _ = build_sorted(verts, tris, depth=depth, leaf_size=4)
            assert len(indices) == len(tris) * 3 * (2 ** depth), depth

    def test_a_coplanar_sheet_cannot_be_split(self):
        """No face plane separates a flat grid, so it degenerates to one leaf
        rather than recursing on a split that does not exist."""
        verts, tris = grid()
        _, indices, data = build_sorted(verts, tris, depth=3, leaf_size=4)
        assert len(data.clusters) == 1
        assert len(indices) == len(tris) * 3


class TestOrdering:
    def _score(self, mesh, cameras=32):
        good = total = 0
        for camera in camera_positions(mesh.verts, cameras):
            drawn = walk(mesh.sorted_data, camera)
            depths = []
            for pi in drawn:
                prim = mesh.primitives[pi]
                idx = mesh.indices[prim.start : prim.start + prim.num_elements]
                used = set(idx)
                centre = tuple(
                    sum(mesh.verts[i][axis] for i in used) / len(used) for axis in range(3)
                )
                depths.append(math.dist(centre, camera))
            good += sum(1 for i in range(len(depths) - 1) if depths[i] >= depths[i + 1] - 1e-6)
            total += max(0, len(depths) - 1)
        # a mesh that draws one primitive has no pair to score, and a ratio
        # over nothing would pass anything
        assert total > 20, total
        return good / total

    def test_beats_the_shipped_median(self):
        """0.576 is what the original tool achieves across the corpus."""
        assert self._score(build(*cards(), leaf_size=2)) > 0.576

    def test_depth_zero_orders_nothing(self):
        """One leaf, drawn in array order, whatever the camera -- which is what
        makes the comparison above mean something."""
        mesh = build(*cards(), depth=0)
        assert len(mesh.sorted_data.clusters) == 1
        assert self._score(mesh) < 0.576


class TestFlat:
    def test_one_cluster_covers_everything(self):
        verts, tris = grid()
        prims, indices, _ = build_sorted(verts, tris, depth=0)
        data = flat_sorted(prims, verts)
        assert len(data.clusters) == 1
        start, end, *_, front, back = data.clusters[0]
        assert (start, end) == (0, len(prims))
        assert front == back == -1

    def test_it_still_walks(self):
        verts, tris = grid()
        prims, indices, _ = build_sorted(verts, tris, depth=0)
        mesh = _Mesh(verts, prims, indices, flat_sorted(prims, verts))
        for camera in camera_positions(verts, 8):
            assert walk(mesh.sorted_data, camera) == list(range(len(prims)))


class TestDegenerate:
    def test_no_triangles(self):
        prims, indices, data = build_sorted([], [])
        assert prims == [] and indices == []
        assert data.start_cluster and data.start_cluster[0] >= -1

    def test_a_single_triangle(self):
        verts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
        tris = [(0, 1, 2, material_word(0))]
        mesh = build(verts, tris)
        assert len(mesh.primitives) == 1
        for camera in camera_positions(verts, 8):
            assert walk(mesh.sorted_data, camera) == [0]

    def test_all_triangles_coplanar_and_degenerate(self):
        """Zero-area triangles give no usable plane; it has to fall back to a
        leaf rather than recurse forever."""
        verts = [(0.0, 0.0, 0.0)] * 3
        tris = [(0, 1, 2, material_word(0))] * 40
        mesh = build(verts, tris, leaf_size=4)
        assert len(mesh.sorted_data.clusters) == 1

    def test_materials_stay_separate_within_a_leaf(self):
        verts, tris = grid(2, 2)
        tris = [(a, b, c, material_word(i % 2)) for i, (a, b, c, _) in enumerate(tris)]
        prims, _, _ = build_sorted(verts, tris, depth=0)
        assert len({p.mat_index for p in prims}) == 2

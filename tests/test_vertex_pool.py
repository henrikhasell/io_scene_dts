"""The shared LOD vertex pool (mapping/vertex_pool.py).

Importable without Blender -- the pool is arithmetic, and keeping it that way
is why it lives in its own module rather than inside blender_to_shape.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mapping.vertex_pool import VertexPool  # noqa: E402


def v(x, y=0.0, z=0.0):
    return (x, y, z)


class TestInterning:
    def test_identical_vertices_share_an_index(self):
        pool = VertexPool()
        a = pool.intern(v(1.0), (0.5, 0.5), v(0.0, 0.0, 1.0))
        b = pool.intern(v(1.0), (0.5, 0.5), v(0.0, 0.0, 1.0))
        assert a == b == 0
        assert len(pool) == 1

    def test_a_different_uv_is_a_different_vertex(self):
        pool = VertexPool()
        pool.intern(v(1.0), (0.5, 0.5), v(0.0, 0.0, 1.0))
        pool.intern(v(1.0), (0.25, 0.5), v(0.0, 0.0, 1.0))
        assert len(pool) == 2

    def test_a_different_normal_is_a_different_vertex(self):
        pool = VertexPool()
        pool.intern(v(1.0), (0.5, 0.5), v(0.0, 0.0, 1.0))
        pool.intern(v(1.0), (0.5, 0.5), v(0.0, 1.0, 0.0))
        assert len(pool) == 2

    def test_differences_below_the_quantum_collapse(self):
        pool = VertexPool()
        pool.intern(v(1.0), (0.5, 0.5), v(0.0, 0.0, 1.0))
        pool.intern(v(1.0 + 1e-9), (0.5, 0.5), v(0.0, 0.0, 1.0))
        assert len(pool) == 1

    def test_split_keeps_coincident_vertices_apart(self):
        """Shape keys need this: two vertices identical in the basis can move
        apart in a later frame, and merging them would lose one frame track."""
        pool = VertexPool()
        a = pool.intern(v(1.0), (0.5, 0.5), v(0.0, 0.0, 1.0), split=3)
        b = pool.intern(v(1.0), (0.5, 0.5), v(0.0, 0.0, 1.0), split=7)
        assert a != b
        assert len(pool) == 2

    def test_the_stored_value_is_the_one_first_seen(self):
        """Quantization decides identity; it must not round the coordinate."""
        pool = VertexPool()
        pool.intern(v(1.0 / 3.0), (0.5, 0.5), v(0.0, 0.0, 1.0))
        assert pool.verts[0][0] == 1.0 / 3.0


class TestPrefixes:
    def _lod(self, pool, xs, split=None):
        for x in xs:
            pool.intern(v(float(x)), (0.0, 0.0), v(0.0, 0.0, 1.0))
        return pool.seal()

    def test_each_level_is_a_prefix_of_the_next(self):
        pool = VertexPool()
        # lowest detail first, each larger level a superset of the last
        low = self._lod(pool, [0, 1])
        mid = self._lod(pool, [0, 1, 2, 3])
        high = self._lod(pool, [0, 1, 2, 3, 4, 5])
        assert (low, mid, high) == (2, 4, 6)
        assert pool.verts[:low] == pool.verts[:low]
        assert pool.verts[:mid][:low] == pool.verts[:low]
        assert pool.verts[:high][:mid] == pool.verts[:mid]

    def test_sealed_lengths_never_shrink(self):
        pool = VertexPool()
        lengths = [self._lod(pool, range(n)) for n in (1, 3, 2, 8)]
        assert lengths == sorted(lengths), lengths

    def test_a_level_reusing_nothing_still_nests(self):
        """Disjoint LODs are the worst case and still produce valid prefixes --
        the file just carries vertices the larger level does not use."""
        pool = VertexPool()
        low = self._lod(pool, [0, 1])
        high = self._lod(pool, [10, 11, 12])
        assert low == 2 and high == 5
        assert pool.verts[:low] == pool.verts[:2]
        assert pool.unused_in_last_slot == 2

    def test_a_level_reusing_everything_wastes_nothing(self):
        pool = VertexPool()
        self._lod(pool, [0, 1, 2])
        self._lod(pool, [0, 1, 2, 3])
        assert pool.unused_in_last_slot == 0

    def test_the_pool_never_exceeds_the_sum_of_its_levels(self):
        """Why sharing is never a size loss: the pool is a union."""
        pool = VertexPool()
        levels = [[0, 1], [0, 1, 2, 3], [2, 3, 4]]
        total = 0
        for xs in levels:
            self._lod(pool, xs)
            total += len(xs)
        assert len(pool) <= total

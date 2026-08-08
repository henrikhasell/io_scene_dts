"""The two draw-order rules, asked of a Shape and of the corpus.

Both live in dtslib because both are answered from the finished shape alone,
which is what lets the corpus be measured against them here rather than only
asserted about in Blender.
"""

from pathlib import Path

import pytest

from dtslib import (
    DtsUnsupportedVersion,
    PRIM_INDEXED,
    PRIM_TRIANGLES,
    Decal,
    Material,
    Mesh,
    Object,
    Primitive,
    Shape,
    read_shape_file,
)
from dtslib.types import MAT_TRANSLUCENT, DecalMeshData
from dtslib.translucency import (
    objects_out_of_order,
    shape_has_translucent_mesh,
    translucent_object_indices,
)
from tests.conftest import corpus_dts_files


def _mesh(mat_index):
    mesh = Mesh()
    mesh.primitives = [Primitive(0, 3, PRIM_TRIANGLES | PRIM_INDEXED | mat_index)]
    mesh.indices = [0, 1, 2]
    return mesh


def _shape(mesh_mats, *, translucent_mats=()):
    """One object per mesh, one material per index named in *mesh_mats*."""
    shape = Shape()
    shape.materials = [
        Material(name=f"m{i}", flags=MAT_TRANSLUCENT if i in translucent_mats else 0)
        for i in range(max(mesh_mats) + 1)
    ]
    for i, mat in enumerate(mesh_mats):
        shape.meshes.append(_mesh(mat))
        shape.objects.append(Object(name_index=0, num_meshes=1, start_mesh_index=i, node_index=0))
    shape.sub_shape_first_object = [0]
    shape.sub_shape_num_objects = [len(shape.objects)]
    return shape


class TestTranslucentObjects:
    def test_an_object_is_translucent_when_a_material_it_draws_with_is(self):
        shape = _shape([0, 1, 0], translucent_mats={1})
        assert translucent_object_indices(shape) == {1}

    def test_no_translucent_material_means_no_translucent_object(self):
        assert translucent_object_indices(_shape([0, 1, 2])) == set()

    def test_one_translucent_level_is_enough(self):
        """An object is one draw call per detail level, so any level counts."""
        shape = _shape([0], translucent_mats={1})
        shape.materials.append(Material(name="glass", flags=MAT_TRANSLUCENT))
        shape.meshes.append(_mesh(len(shape.materials) - 1))
        shape.objects[0].num_meshes = 2
        assert translucent_object_indices(shape) == {0}


class TestOrdering:
    def test_opaque_after_translucent_is_out_of_order(self):
        shape = _shape([0, 1, 0], translucent_mats={1})
        assert objects_out_of_order(shape) == [2]

    def test_translucent_last_is_in_order(self):
        assert objects_out_of_order(_shape([0, 0, 1], translucent_mats={1})) == []

    def test_all_translucent_is_in_order(self):
        assert objects_out_of_order(_shape([1, 1], translucent_mats={1})) == []

    def test_sub_shapes_are_judged_separately(self):
        """An opaque object in sub-shape 1 is not behind sub-shape 0's glass:
        the list is sliced per sub-shape and only ever drawn a slice at a time."""
        shape = _shape([0, 1, 0, 0], translucent_mats={1})
        shape.sub_shape_first_object = [0, 2]
        shape.sub_shape_num_objects = [2, 2]
        assert objects_out_of_order(shape) == []


class TestDecalsNeedTranslucency:
    def test_an_opaque_shape_has_nothing_translucent(self):
        assert not shape_has_translucent_mesh(_shape([0, 1]))

    def test_a_translucent_object_mesh_counts(self):
        assert shape_has_translucent_mesh(_shape([0, 1], translucent_mats={1}))

    def test_a_translucent_decal_mesh_counts(self):
        """The 59 corpus shapes whose only translucency is the decal's own
        material -- a decal keeps it on decal_data, not in a primitive, so a
        scan of primitives alone would call these shapes opaque."""
        shape = _shape([0], translucent_mats={1})
        shape.materials.append(Material(name="scorch", flags=MAT_TRANSLUCENT))
        decal_mesh = Mesh()
        decal_mesh.decal_data = DecalMeshData()
        decal_mesh.decal_data.material_index = PRIM_INDEXED | (len(shape.materials) - 1)
        shape.meshes.append(decal_mesh)
        shape.decals.append(Decal(raw=(0, 1, 1, 0, -1)))
        assert shape_has_translucent_mesh(shape)


@pytest.mark.corpus
@pytest.mark.parametrize("path", corpus_dts_files(), ids=lambda p: Path(p).name)
def test_every_decal_shape_has_something_translucent(path):
    """The rule the exporter refuses on, checked against shipped art: of the
    153 decal-bearing corpus shapes, not one is entirely opaque.  94 carry the
    translucency on a regular object mesh and the other 59 on the decal's own
    material, which is why both count."""
    try:
        shape = read_shape_file(path)
    except DtsUnsupportedVersion:
        pytest.skip("v15/v16, refused by the reader")
    if not shape.decals:
        pytest.skip("no decals")
    assert shape_has_translucent_mesh(shape)

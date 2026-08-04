"""Structural shape comparison for round-trip tests."""

import dataclasses

from dtslib import Mesh, Shape

# fields that legitimately change across a rewrite
_IGNORED_SHAPE_FIELDS = {"source_version", "exporter_version", "pad16", "pad8"}


def _mesh_norm(mesh, other):
    """Meshes from v<=21 files have no encoded normals on disk; a v23/v24
    rewrite synthesizes them.  Treat synthesized-vs-absent as equal."""
    if mesh is None or other is None:
        return mesh
    changes = {}
    if not mesh.encoded_norms and other.encoded_norms:
        changes["encoded_norms"] = other.encoded_norms
    if not mesh.initial_encoded_norms and other.initial_encoded_norms:
        changes["initial_encoded_norms"] = other.initial_encoded_norms
    return dataclasses.replace(mesh, **changes) if changes else mesh


def assert_shapes_equal(a: Shape, b: Shape) -> None:
    for f in dataclasses.fields(a):
        if f.name in _IGNORED_SHAPE_FIELDS:
            continue
        va, vb = getattr(a, f.name), getattr(b, f.name)
        if f.name == "meshes":
            assert len(va) == len(vb), f"mesh count {len(va)} != {len(vb)}"
            for i, (ma, mb) in enumerate(zip(va, vb)):
                ma = _mesh_norm(ma, mb)
                assert ma == mb, f"mesh {i} differs: {_first_mesh_diff(ma, mb)}"
        else:
            assert va == vb, f"shape field {f.name!r} differs"


def _first_mesh_diff(a: Mesh, b: Mesh) -> str:
    if a is None or b is None:
        return f"{'a' if a is None else 'b'} is null"
    for f in dataclasses.fields(a):
        if getattr(a, f.name) != getattr(b, f.name):
            return f.name
    return "?"

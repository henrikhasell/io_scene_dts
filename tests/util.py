"""Structural shape comparison for round-trip tests."""

import dataclasses

from dtslib import Mesh, Shape

# fields that legitimately change across a rewrite
_IGNORED_SHAPE_FIELDS = {"source_version", "exporter_version", "pad16", "pad8"}


@dataclasses.dataclass(frozen=True)
class Diff:
    """One structural difference between two shapes."""

    field: str  # e.g. "nodes" or "meshes[12].verts"
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.field}: {self.detail}" if self.detail else self.field


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


def _ignored(value, ignore):
    """Nested (type name, field name) pairs to skip, e.g. ("Node", "runtime")."""
    if not ignore:
        return frozenset()
    name = type(value).__name__
    return frozenset(f for t, f in ignore if t == name)


def _equal(a, b, tol, ignore):
    """Recursive comparison with an optional float tolerance.

    With ``tol == 0`` and no ``ignore`` this is plain ``==``; the recursive walk
    only kicks in when a caller actually asks for slack, so the strict path
    stays exact and fast.
    """
    if not tol and not ignore:
        return a == b
    if isinstance(a, float) and isinstance(b, float):
        return a == b or abs(a - b) <= tol
    if isinstance(a, (bytes, bytearray, str)) or isinstance(b, (bytes, bytearray, str)):
        return a == b
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_equal(x, y, tol, ignore) for x, y in zip(a, b))
    if dataclasses.is_dataclass(a) and dataclasses.is_dataclass(b):
        if type(a) is not type(b):
            return False
        skip = _ignored(a, ignore)
        return all(
            _equal(getattr(a, f.name), getattr(b, f.name), tol, ignore)
            for f in dataclasses.fields(a)
            if f.name not in skip
        )
    return a == b


def _mesh_diff_fields(a: Mesh, b: Mesh, tol=0.0, ignore=frozenset()) -> list[str]:
    """Names of every differing field between two meshes."""
    if a is None or b is None:
        return [] if a is b else ["<null>"]
    skip = _ignored(a, ignore)
    return [
        f.name
        for f in dataclasses.fields(a)
        if f.name not in skip
        and not _equal(getattr(a, f.name), getattr(b, f.name), tol, ignore)
    ]


def compare_shapes(
    a: Shape,
    b: Shape,
    *,
    tol: float = 0.0,
    ignore=frozenset(),
    ignore_fields=frozenset(),
) -> list[Diff]:
    """Every structural difference between two shapes, as a list.

    Unlike :func:`assert_shapes_equal` this does not stop at the first
    difference -- a corpus sweep needs the whole picture per file, not just the
    first field that happened to diverge.

    ``tol`` allows float slack (Blender round-trips through 32-bit floats and
    its own math); ``ignore`` takes ``(type name, field name)`` pairs to skip
    nested engine-scratch fields; ``ignore_fields`` skips top-level Shape
    fields.  All three default to strict.
    """
    diffs: list[Diff] = []
    for f in dataclasses.fields(a):
        if f.name in _IGNORED_SHAPE_FIELDS or f.name in ignore_fields:
            continue
        va, vb = getattr(a, f.name), getattr(b, f.name)
        if f.name == "meshes":
            if len(va) != len(vb):
                diffs.append(Diff("meshes", f"count {len(va)} != {len(vb)}"))
                continue
            for i, (ma, mb) in enumerate(zip(va, vb)):
                ma = _mesh_norm(ma, mb)
                if _equal(ma, mb, tol, ignore):
                    continue
                fields = _mesh_diff_fields(ma, mb, tol, ignore)
                diffs.append(Diff(f"meshes[{i}]", ", ".join(fields) or "?"))
        elif not _equal(va, vb, tol, ignore):
            detail = ""
            if isinstance(va, (list, tuple)) and isinstance(vb, (list, tuple)) and len(va) != len(vb):
                detail = f"length {len(va)} != {len(vb)}"
            diffs.append(Diff(f.name, detail))
    return diffs


def assert_shapes_equal(a: Shape, b: Shape) -> None:
    """Strict structural equality -- unchanged bar, now sharing one rule."""
    diffs = compare_shapes(a, b)
    if not diffs:
        return
    first = diffs[0]
    if first.field == "meshes" and first.detail.startswith("count "):
        raise AssertionError(f"mesh count {first.detail[len('count '):]}")
    if first.field.startswith("meshes["):
        i = first.field[len("meshes[") : -1]
        raise AssertionError(f"mesh {i} differs: {first.detail.split(', ')[0]}")
    raise AssertionError(f"shape field {first.field!r} differs")


def _first_mesh_diff(a: Mesh, b: Mesh) -> str:
    fields = _mesh_diff_fields(a, b)
    return fields[0] if fields else "?"

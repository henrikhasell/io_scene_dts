"""dtslib must stay importable under plain CPython — no bpy anywhere."""

from pathlib import Path

DTSLIB = Path(__file__).resolve().parent.parent / "dtslib"


def test_dtslib_has_no_bpy_imports():
    for path in DTSLIB.rglob("*.py"):
        text = path.read_text()
        for line in text.splitlines():
            stripped = line.split("#")[0].strip()
            assert not stripped.startswith(("import bpy", "from bpy")), (
                f"{path.name} imports bpy: {line!r}"
            )
            assert "importlib.import_module('bpy'" not in stripped

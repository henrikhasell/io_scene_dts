"""Shared helpers for the headless Blender runners.

Both ``run_blender_tests.py`` (scenario suite) and ``roundtrip_sweep.py``
(corpus sweep) drive the add-on the same way; keeping the primitives here stops
the two entry points from drifting apart.
"""

import sys
import tempfile
from pathlib import Path

import bpy

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"


def add_repo_to_path():
    """Make both ``io_scene_dts.*`` (the add-on) and ``dtslib``/``tests``
    (the checkout, used bare) importable."""
    for p in (str(REPO.parent), str(REPO)):
        if p not in sys.path:
            sys.path.insert(0, p)


def start_coverage(suffix):
    """Coverage inside the Blender process, or None if unavailable.

    ``data_suffix`` keeps each run's data separate so ``coverage combine``
    merges rather than overwrites.
    """
    for candidate in sorted(REPO.glob(".venv/lib/python*/site-packages")):
        if str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
    try:
        import coverage
    except ImportError:
        print("coverage not available inside Blender; running without it")
        return None
    cov = coverage.Coverage(
        config_file=str(REPO / "pyproject.toml"),
        data_file=str(REPO / ".coverage"),
        data_suffix=suffix,
    )
    cov.start()
    return cov


def reset():
    bpy.ops.wm.read_homefile(use_empty=True)


def tmp_path(suffix):
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    f.close()
    return f.name


def armature():
    return next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")


def import_dts(path, **kw):
    res = bpy.ops.io_scene_dts.import_dts(filepath=str(path), **kw)
    assert res == {"FINISHED"}, res
    return armature()


def export_dts(path, version="24", **kw):
    res = bpy.ops.io_scene_dts.export_dts(filepath=str(path), version=version, **kw)
    assert res == {"FINISHED"}, res
    return path

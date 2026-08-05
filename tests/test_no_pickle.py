"""Nothing the add-on ships may unpickle.

Mesh data that Blender could not rebuild used to ride through a .blend as a
base64'd ``pickle.dumps`` of a ``dtslib.Mesh``, replayed verbatim on export.
That made the data invisible and uneditable, and it put ``pickle.loads`` on a
code path fed by whatever .blend the user happened to open -- which is arbitrary
code execution, not a storage format.

Everything it carried is derived on export now: vertex sharing by
``mapping/vertex_pool.py``, cluster trees by ``dtslib/sorted_build.py``, and
strip packing not at all (measured at x1.00).  This keeps it that way.

Modelled on test_no_bpy.py, and scoped the same way: the files the manifest
actually ships, not the analysis scripts or the tests.
"""

import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHIPPED_DIRS = ("dtslib", "mapping", "ops")
SHIPPED_FILES = ("__init__.py",)

BANNED = ("import pickle", "from pickle", "pickle.loads", "pickle.dumps", "b64decode")


def shipped_python_files():
    for name in SHIPPED_FILES:
        yield REPO / name
    for directory in SHIPPED_DIRS:
        yield from sorted((REPO / directory).rglob("*.py"))


def test_the_shipped_add_on_does_not_unpickle():
    for path in shipped_python_files():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#")[0]
            for banned in BANNED:
                assert banned not in code, (
                    f"{path.relative_to(REPO)}:{number} uses {banned!r} -- "
                    f"mesh data belongs in Blender data, not in a payload"
                )


def test_the_scan_covers_what_the_manifest_ships():
    """A directory added to the add-on but not to SHIPPED_DIRS would leave this
    test passing while checking nothing."""
    manifest = tomllib.loads((REPO / "blender_manifest.toml").read_text())
    excluded = manifest.get("build", {}).get("paths_exclude_pattern", [])
    scanned = set(SHIPPED_DIRS)
    for entry in REPO.iterdir():
        if not entry.is_dir() or entry.name.startswith((".", "_")):
            continue
        if any(entry.name in pattern for pattern in excluded):
            continue
        assert entry.name in scanned, (
            f"{entry.name}/ ships but is not scanned for pickling; add it to SHIPPED_DIRS"
        )

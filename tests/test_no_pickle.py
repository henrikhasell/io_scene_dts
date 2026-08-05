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

import io
import tokenize
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHIPPED_DIRS = ("dtslib", "mapping", "ops", "props", "ui")
SHIPPED_FILES = ("__init__.py",)

BANNED = ("pickle", "b64decode")


def shipped_python_files():
    for name in SHIPPED_FILES:
        yield REPO / name
    for directory in SHIPPED_DIRS:
        yield from sorted((REPO / directory).rglob("*.py"))


def code_names(path):
    """(line, name) for every identifier in the file, skipping strings.

    Tokenized rather than grepped so that prose can say the word: the migration
    code explains at length why it does *not* unpickle the old payload, and a
    substring scan would flag the explanation.
    """
    source = path.read_text()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NAME:
            yield token.start[0], token.string


def test_the_shipped_add_on_does_not_unpickle():
    for path in shipped_python_files():
        for number, name in code_names(path):
            assert name not in BANNED, (
                f"{path.relative_to(REPO)}:{number} refers to {name!r} in code -- "
                f"mesh data belongs in Blender data, not in a payload"
            )


def test_the_scan_reads_code_and_not_prose(tmp_path):
    """The scan has to be able to tell the two apart, or the guarantee it gives
    is only that nobody mentioned pickling."""
    prose = tmp_path / "prose.py"
    prose.write_text('"""We deliberately never call pickle.loads here."""\nX = 1\n')
    assert all(name not in BANNED for _, name in code_names(prose))

    code = tmp_path / "code.py"
    code.write_text("import pickle\n")
    assert any(name in BANNED for _, name in code_names(code))


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

"""On-disk corpus discovery, shared by the pytest suite and the Blender sweeps.

This module deliberately imports neither ``pytest`` nor ``bpy``: the headless
Blender runners under ``tests/blender/`` execute outside pytest and cannot pick
up ``conftest.py``, but they still need the same corpus roots and the same
selection rules.  ``tests/conftest.py`` re-exports everything here.
"""

import glob
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dtslib import read_header  # noqa: E402

# on-disk corpora, referenced in place (skip-if-missing)
T2_SHAPES = Path("/home/henrik/Documents/Repositories/hasell-engine/t2/shapes")
HASELL_FILES = Path("/home/henrik/Documents/Repositories/hasell-engine/files")
MYGAME = Path("/home/henrik/Documents/Repositories/agentic-torque/mygame")

DTS_ROOTS = (T2_SHAPES, HASELL_FILES, MYGAME)

# the reader's minimum length; also drops the 0-byte xorg2.dts in t2/shapes
MIN_SIZE = 16


def _collect(*roots, pattern):
    files = set()
    for root in roots:
        if root.is_dir():
            files.update(glob.glob(str(root / "**" / pattern), recursive=True))
    return sorted(p for p in files if Path(p).stat().st_size >= MIN_SIZE)


def corpus_dts_files():
    return _collect(*DTS_ROOTS, pattern="*.dts")


def corpus_dsq_files():
    return _collect(*DTS_ROOTS, pattern="*.dsq")


def dts_version(path) -> int | None:
    """Version from the 4-byte header, or None if it cannot be read.

    Cheap by design -- no full parse, so this is safe to run across a whole
    corpus just to bucket files by version.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read(4)
    except OSError:
        return None
    if len(data) < 4:
        return None
    try:
        return read_header(data)[0]
    except Exception:
        return None


def corpus_dts_files_of_version(version: int, *roots):
    """Corpus .dts paths whose header reports ``version``.

    Computed from the files on disk rather than hardcoded, so the selection
    tracks the corpus instead of drifting away from it.
    """
    paths = _collect(*(roots or DTS_ROOTS), pattern="*.dts")
    return [p for p in paths if dts_version(p) == version]

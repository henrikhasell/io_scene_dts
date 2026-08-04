import glob
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

FIXTURES = REPO_ROOT / "tests" / "fixtures"

# on-disk corpora, referenced in place (skip-if-missing)
T2_SHAPES = Path("/home/henrik/Documents/Repositories/hasell-engine/t2/shapes")
HASELL_FILES = Path("/home/henrik/Documents/Repositories/hasell-engine/files")
MYGAME = Path("/home/henrik/Documents/Repositories/agentic-torque/mygame")


def fixture(name: str) -> Path:
    return FIXTURES / name


def fixture_bytes(name: str) -> bytes:
    return fixture(name).read_bytes()


def _collect(*roots, pattern):
    files = set()
    for root in roots:
        if root.is_dir():
            files.update(glob.glob(str(root / "**" / pattern), recursive=True))
    return sorted(p for p in files if Path(p).stat().st_size >= 16)


def corpus_dts_files():
    return _collect(T2_SHAPES, HASELL_FILES, MYGAME, pattern="*.dts")


def corpus_dsq_files():
    return _collect(T2_SHAPES, HASELL_FILES, MYGAME, pattern="*.dsq")


def pytest_collection_modifyitems(config, items):
    if not corpus_dts_files():
        skip = pytest.mark.skip(reason="DTS corpus not present on this machine")
        for item in items:
            if "corpus" in item.keywords:
                item.add_marker(skip)

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# corpus discovery lives in a pytest-free module so the headless Blender
# runners under tests/blender/ can share it; re-exported here for the suite
from tests.corpus import (  # noqa: E402,F401
    HASELL_FILES,
    MYGAME,
    T2_SHAPES,
    _collect,
    corpus_dsq_files,
    corpus_dts_files,
    corpus_dts_files_of_version,
    corpus_unique_dts_files,
    dts_version,
)

FIXTURES = REPO_ROOT / "tests" / "fixtures"


def fixture(name: str) -> Path:
    return FIXTURES / name


def fixture_bytes(name: str) -> bytes:
    return fixture(name).read_bytes()


def pytest_collection_modifyitems(config, items):
    if not corpus_dts_files():
        skip = pytest.mark.skip(reason="DTS corpus not present on this machine")
        for item in items:
            if "corpus" in item.keywords:
                item.add_marker(skip)

"""Node/Object/Decal link words must be derivable, not carried."""

import pytest

from dtslib import read_shape
from dtslib.runtime_links import recompute_runtime_links
from tests.conftest import fixture_bytes
from tests.corpus import corpus_dts_files

FIXTURE_SHAPES = [
    "v23_crt_monitor.dts",
    "v24_detail_levels.dts",
    "v24_test_crate.dts",
    "v24_skin_animation.dts",
]


def _links(shape):
    return (
        [tuple(n.runtime) for n in shape.nodes],
        [tuple(o.runtime) for o in shape.objects],
        [d.raw[4] for d in shape.decals],
    )


@pytest.mark.parametrize("name", FIXTURE_SHAPES)
def test_recompute_matches_fixture(name):
    shape = read_shape(fixture_bytes(name))
    before = _links(shape)
    recompute_runtime_links(shape)
    assert _links(shape) == before, f"{name}: recomputed links differ from the file"


def test_recompute_is_idempotent():
    shape = read_shape(fixture_bytes("v24_skin_animation.dts"))
    recompute_runtime_links(shape)
    once = _links(shape)
    recompute_runtime_links(shape)
    assert _links(shape) == once


@pytest.mark.corpus
def test_recompute_matches_corpus():
    """Across the corpus, recomputed links must equal what the file stores.

    Shapes with no links set at all were written by a tool that never linked
    them (the engine fills those in at load), so they are not counterexamples.
    """
    from pathlib import Path

    checked = 0
    mismatches = []
    for path in corpus_dts_files():
        try:
            shape = read_shape(Path(path).read_bytes())
        except Exception:
            continue
        before = _links(shape)
        if not any(v != (-1, -1, -1) for v in before[0]):
            continue
        recompute_runtime_links(shape)
        if _links(shape) != before:
            mismatches.append(Path(path).name)
        checked += 1
    assert checked > 100, f"only {checked} shapes had stored links"
    assert not mismatches, f"{len(mismatches)} shapes differ, e.g. {mismatches[:3]}"

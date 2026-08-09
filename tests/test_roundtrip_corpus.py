"""Golden-file sweep over the on-disk corpora (skipped if not present).

Policy:
- every v15-24 .dts parses; v14 and older, and v25 and newer, are refused
- a same-version rewrite is byte-identical, with three documented exceptions
  (``NOT_BYTE_IDENTICAL``)
- every file converts to every version 15-24 and reads back equal to what
  ``fit_to_version`` said would go in -- 302 distinct shapes x 10 versions
- every v17+ .dsq parses; v22+ rewrites are byte-identical
"""

import copy
from pathlib import Path

import pytest

from dtslib import (
    DtsUnsupportedVersion,
    fit_to_version,
    read_dsq,
    read_header,
    read_shape,
    write_dsq,
    write_shape,
)
from tests.conftest import corpus_dsq_files, corpus_dts_files, corpus_unique_dts_files
from tests.util import assert_shapes_equal, compare_shapes

DTS_FILES = corpus_dts_files()
# 856 paths, 302 distinct files: the ten-version sweep costs about a second per
# file, so it runs on the distinct ones
UNIQUE_DTS_FILES = corpus_unique_dts_files()
DSQ_FILES = corpus_dsq_files()

ALL_VERSIONS = tuple(range(15, 25))

# Shapes whose own bytes cannot be reproduced, and what stands in the way.  All
# three are values the engine reads and discards, so a rewrite is still the same
# shape -- keeping them would mean carrying uneditable junk through Blender.
NOT_BYTE_IDENTICAL = {
    # pre-v17: an obsolete per-node bool, and the two obsolete membership sets
    # in every sequence record (both written as zeros / trimmed)
    "chaingun_shot.dts": 15,
    "borg3.dts": 16,
    "borg11.dts": 16,
    # pre-v20: the empty mesh header in front of every decal, which is
    # uninitialized memory in the original (0xcdcdcdcd) and zeros here
    "vehicle_air_scout_wreck.dts": 19,
}


def _id(path):
    return Path(path).name


@pytest.mark.corpus
@pytest.mark.parametrize("path", DTS_FILES, ids=_id)
def test_dts_roundtrip(path):
    data = Path(path).read_bytes()
    version, _ = read_header(data)
    if version < 15 or version > 24:
        with pytest.raises(DtsUnsupportedVersion):
            read_shape(data)
        return
    shape = read_shape(data)
    out = write_shape(shape, version, shape.exporter_version)
    if NOT_BYTE_IDENTICAL.get(Path(path).name) == version:
        assert_shapes_equal(shape, read_shape(out))
    else:
        assert out == data, f"{path}: v{version} rewrite not byte-identical"


@pytest.mark.corpus
@pytest.mark.parametrize("version", ALL_VERSIONS)
@pytest.mark.parametrize("path", UNIQUE_DTS_FILES, ids=_id)
def test_dts_convert_to_every_version(path, version):
    """The load-bearing claim: any shape, any version, no surprises.

    Compared against the *fitted* shape rather than the original, because that
    is the whole point of ``fit_to_version`` -- it says up front what the target
    version cannot hold, so what comes back has to match it exactly.
    """
    shape = read_shape(Path(path).read_bytes())
    fitted = copy.deepcopy(shape)
    fit_to_version(fitted, version)
    data = write_shape(fitted, version)
    assert read_header(data)[0] == version
    diffs = compare_shapes(fitted, read_shape(data))
    assert not diffs, f"{path} -> v{version}: {'; '.join(str(d) for d in diffs[:4])}"


@pytest.mark.corpus
@pytest.mark.parametrize("path", DSQ_FILES, ids=_id)
def test_dsq_roundtrip(path):
    data = Path(path).read_bytes()
    dsq = read_dsq(data)
    if dsq.version >= 22:
        assert write_dsq(dsq, dsq.version) == data, f"{path}: DSQ rewrite not byte-identical"

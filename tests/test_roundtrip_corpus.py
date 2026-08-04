"""Golden-file sweep over the on-disk corpora (skipped if not present).

Policy:
- every v19-24 .dts parses
- v23->v23 and v24->v24 rewrites are byte-identical to the original
- v19-22 files convert to v23/v24 and re-read structurally equal
- every v17+ .dsq parses; v22+ rewrites are byte-identical
"""

from pathlib import Path

import pytest

from dtslib import (
    DtsUnsupportedVersion,
    read_dsq,
    read_header,
    read_shape,
    write_dsq,
    write_shape,
)
from tests.conftest import corpus_dsq_files, corpus_dts_files
from tests.util import assert_shapes_equal

DTS_FILES = corpus_dts_files()
DSQ_FILES = corpus_dsq_files()


def _id(path):
    return Path(path).name


@pytest.mark.corpus
@pytest.mark.parametrize("path", DTS_FILES, ids=_id)
def test_dts_roundtrip(path):
    data = Path(path).read_bytes()
    version, _ = read_header(data)
    if version < 17 or version > 24:
        with pytest.raises(DtsUnsupportedVersion):
            read_shape(data)
        return
    shape = read_shape(data)
    if version in (23, 24):
        assert write_shape(shape, version, shape.exporter_version) == data, (
            f"{path}: v{version} rewrite not byte-identical"
        )
    elif version >= 19:
        target = 24 if shape.ground_translations else 23
        assert_shapes_equal(shape, read_shape(write_shape(shape, target)))
    else:
        # old flat-stream format (17/18): converted structure must survive
        target = 24 if shape.ground_translations else 23
        assert_shapes_equal(shape, read_shape(write_shape(shape, target)))


@pytest.mark.corpus
@pytest.mark.parametrize("path", DSQ_FILES, ids=_id)
def test_dsq_roundtrip(path):
    data = Path(path).read_bytes()
    dsq = read_dsq(data)
    if dsq.version >= 22:
        assert write_dsq(dsq, dsq.version) == data, f"{path}: DSQ rewrite not byte-identical"

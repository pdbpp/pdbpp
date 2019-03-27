import sys

import pytest

_orig_trace = None


def pytest_configure():
    global _orig_trace
    _orig_trace = sys.gettrace()


# if _orig_trace and not hasattr(sys, "pypy_version_info"):
# Fails with PyPy2 (https://travis-ci.org/antocuni/pdb/jobs/509624590)?!
@pytest.fixture(autouse=True)
def restore_settrace():
    """(Re)store sys.gettrace after test run.

    This is required to re-enable coverage tracking.
    """
    assert sys.gettrace() is _orig_trace

    yield

    newtrace = sys.gettrace()
    if newtrace is not _orig_trace:
        assert newtrace is None
        sys.settrace(_orig_trace)


@pytest.fixture
def tmphome(tmpdir, monkeypatch):
    """Set up HOME in a temporary directory.

    This ignores any real ~/.pdbrc.py then, and seems to be
    required also with linecache on py27, where it would read contents from
    ~/.pdbrc?!.
    """
    monkeypatch.setenv("HOME", str(tmpdir))
    monkeypatch.setenv("USERPROFILE", str(tmpdir))

    with tmpdir.as_cwd():
        yield tmpdir

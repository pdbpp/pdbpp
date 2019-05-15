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
        sys.settrace(_orig_trace)
        assert newtrace is None


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


@pytest.fixture(params=("pyrepl", "readline"), scope="session")
def readline_param(request):
    from _pytest.monkeypatch import MonkeyPatch

    m = MonkeyPatch()

    if request.param == "pyrepl":
        m.setattr("fancycompleter.DefaultConfig.prefer_pyrepl", True)
    else:
        m.setattr("fancycompleter.DefaultConfig.prefer_pyrepl", False)
    return request.param


@pytest.fixture
def monkeypatch_readline(request, monkeypatch, readline_param):
    """Patch readline to return given results."""
    def inner(line, begidx, endidx):
        if readline_param == "pyrepl":
            readline = "pyrepl.readline"
        else:
            assert readline_param == "readline"
            readline = "readline"

        monkeypatch.setattr("%s.get_line_buffer" % readline, lambda: line)
        monkeypatch.setattr("%s.get_begidx" % readline, lambda: begidx)
        monkeypatch.setattr("%s.get_endidx" % readline, lambda: endidx)

    return inner

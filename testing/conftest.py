import functools
import sys

import pytest

_orig_trace = None


def pytest_configure():
    global _orig_trace
    _orig_trace = sys.gettrace()


@pytest.fixture(scope="session", autouse=True)
def term():
    """Configure TERM for predictable output from Pygments."""
    from _pytest.monkeypatch import MonkeyPatch

    m = MonkeyPatch()
    m.setenv("TERM", "xterm-256color")
    yield m
    m.undo()


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


@pytest.fixture(autouse=sys.version_info < (3, 6))
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
        try:
            import pyrepl.readline  # noqa: F401
        except ImportError as exc:
            pytest.skip(msg="pyrepl not available: {}".format(exc))
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


@pytest.fixture
def monkeypatch_pdb_methods(monkeypatch):
    def mock(method, *args, **kwargs):
        print("=== %s(%s, %s)" % (method, args, kwargs))

    for mock_method in ("set_trace", "set_continue"):
        monkeypatch.setattr(
            "pdb.pdb.Pdb.%s" % mock_method, functools.partial(mock, mock_method)
        )

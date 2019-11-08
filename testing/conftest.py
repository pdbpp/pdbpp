import functools
import sys
from contextlib import contextmanager

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
def restore_settrace(monkeypatch):
    """(Re)store sys.gettrace after test run.

    This is required to re-enable coverage tracking.
    """
    assert sys.gettrace() is _orig_trace

    orig_settrace = sys.settrace

    # Wrap sys.settrace to restore original tracing function (coverage)
    # with `sys.settrace(None)`.
    def settrace(func):
        if func is None:
            orig_settrace(_orig_trace)
        else:
            orig_settrace(func)
    monkeypatch.setattr("sys.settrace", settrace)

    yield

    newtrace = sys.gettrace()
    if newtrace is not _orig_trace:
        sys.settrace(_orig_trace)
        assert newtrace is None


@pytest.fixture(scope="session")
def _tmphome_path(tmpdir_factory):
    return tmpdir_factory.mktemp("tmphome")


@pytest.fixture(autouse=sys.version_info < (3, 6))
def tmphome(request, monkeypatch):
    """Set up HOME in a temporary directory.

    This ignores any real ~/.pdbrc.py then, and seems to be
    required also with linecache on py27, where it would read contents from
    ~/.pdbrc?!.
    """
    # Use tmpdir from testdir, if it is used.
    if "testdir" in request.fixturenames:
        tmpdir = request.getfixturevalue("testdir").tmpdir
    elif "tmpdir" in request.fixturenames:
        tmpdir = request.getfixturevalue("tmpdir")
    else:
        tmpdir = request.getfixturevalue("_tmphome_path")

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
            "pdbpp.pdb.Pdb.%s" % mock_method, functools.partial(mock, mock_method)
        )


@pytest.fixture
def patched_completions(monkeypatch):
    from .test_pdb import PdbTest
    import fancycompleter

    def inner(text, fancy_comps, pdb_comps):
        _pdb = PdbTest()
        _pdb.reset()
        _pdb.cmdloop = lambda: None
        _pdb.forget = lambda: None

        def _get_comps(complete, text):
            if isinstance(complete.__self__, fancycompleter.Completer):
                return fancy_comps[:]
            return pdb_comps[:]

        monkeypatch.setattr(_pdb, "_get_all_completions", _get_comps)
        _pdb.interaction(sys._getframe(), None)

        comps = []
        while True:
            val = _pdb.complete(text, len(comps))
            if val is None:
                break
            comps += [val]
        return comps

    return inner


@pytest.fixture(params=("color", "nocolor"), scope="session")
def fancycompleter_color_param(request):
    from _pytest.monkeypatch import MonkeyPatch

    m = MonkeyPatch()

    if request.param == "color":
        m.setattr("fancycompleter.DefaultConfig.use_colors", True)
    else:
        m.setattr("fancycompleter.DefaultConfig.use_colors", False)
    return request.param


@pytest.fixture
def monkeypatch_importerror(monkeypatch):
    @contextmanager
    def cm(mocked_imports):
        orig_import = __import__

        def import_mock(name, *args):
            if name in mocked_imports:
                raise ImportError
            return orig_import(name, *args)

        with monkeypatch.context() as m:
            if sys.version_info >= (3,):
                m.setattr('builtins.__import__', import_mock)
            else:
                m.setattr('__builtin__.__import__', import_mock)
            yield m
    return cm

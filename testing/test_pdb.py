# -*- coding: utf-8 -*-
from __future__ import print_function

import bdb
import inspect
import os
import os.path
import re
import sys
import traceback
from io import BytesIO

import py
import pytest

import pdbpp

try:
    from shlex import quote
except ImportError:
    from pipes import quote

try:
    from itertools import zip_longest
except ImportError:
    from itertools import izip_longest as zip_longest


pytest_plugins = ["pytester"]

# Windows support
# The basic idea is that paths on Windows are dumb because of backslashes.
# Typically this would be resolved by using `pathlib`, but we need to maintain
# support for pre-Py36 versions.
# A lot of tests are regex checks and the back-slashed Windows paths end
# up looking like they have escape characters in them (specifically the `\p`
# in `...\pdbpp`). So we need to make sure to escape those strings.
# In addtion, Windows is a case-insensitive file system. Most introspection
# tools return the `normcase` version (eg: all lowercase), so we adjust the
# canonical filename accordingly.
RE_THIS_FILE = re.escape(__file__)
THIS_FILE_CANONICAL = __file__
if sys.platform == 'win32':
    THIS_FILE_CANONICAL = __file__.lower()
RE_THIS_FILE_CANONICAL = re.escape(THIS_FILE_CANONICAL)
RE_THIS_FILE_CANONICAL_QUOTED = re.escape(quote(THIS_FILE_CANONICAL))
RE_THIS_FILE_QUOTED = re.escape(quote(__file__))


class FakeStdin:
    def __init__(self, lines):
        self.lines = iter(lines)

    def readline(self):
        try:
            line = next(self.lines) + '\n'
            sys.stdout.write(line)
            return line
        except StopIteration:
            return ''


class ConfigTest(pdbpp.DefaultConfig):
    highlight = False
    use_pygments = False
    prompt = '# '  # because + has a special meaning in the regexp
    editor = 'emacs'
    stdin_paste = 'epaste'
    disable_pytest_capturing = False
    current_line_color = 44


class ConfigWithHighlight(ConfigTest):
    highlight = True


class ConfigWithPygments(ConfigTest):
    use_pygments = True


class ConfigWithPygmentsNone(ConfigTest):
    use_pygments = None


class ConfigWithPygmentsAndHighlight(ConfigWithPygments, ConfigWithHighlight):
    pass


class PdbTest(pdbpp.Pdb):
    use_rawinput = 1

    def __init__(self, *args, **kwds):
        readrc = kwds.pop("readrc", False)
        nosigint = kwds.pop("nosigint", True)
        kwds.setdefault('Config', ConfigTest)
        if sys.version_info >= (3, 6):
            super(PdbTest, self).__init__(*args, readrc=readrc, **kwds)
        else:
            super(PdbTest, self).__init__(*args, **kwds)
        # Do not install sigint_handler in do_continue by default.
        self.nosigint = nosigint

    def _open_editor(self, editcmd):
        print("RUN %s" % editcmd)

    def _open_stdin_paste(self, cmd, lineno, filename, text):
        print("RUN %s +%d" % (cmd, lineno))
        print(repr(text))

    def do_shell(self, arg):
        """Track when do_shell gets called (via "!").

        This is not implemented by default, but we should not trigger it
        via parseline unnecessarily, which would cause unexpected results
        if somebody uses it.
        """
        print("do_shell_called: %r" % arg)
        return self.default(arg)


def set_trace_via_module(frame=None, cleanup=True, Pdb=PdbTest, **kwds):
    """set_trace helper that goes through pdb.set_trace.

    It injects Pdb into the globals of pdb.set_trace, to use the given frame.
    """
    if frame is None:
        frame = sys._getframe().f_back

    if cleanup:
        pdbpp.cleanup()

    class PdbForFrame(Pdb):
        def set_trace(self, _frame, *args, **kwargs):
            super(PdbForFrame, self).set_trace(frame, *args, **kwargs)

    newglobals = pdbpp.set_trace.__globals__.copy()
    newglobals['Pdb'] = PdbForFrame
    new_set_trace = pdbpp.rebind_globals(pdbpp.set_trace, newglobals)
    new_set_trace(**kwds)


def set_trace(frame=None, cleanup=True, Pdb=PdbTest, **kwds):
    """set_trace helper for tests, going through Pdb.set_trace directly."""
    if frame is None:
        frame = sys._getframe().f_back

    if cleanup:
        pdbpp.cleanup()

    Pdb(**kwds).set_trace(frame)


def xpm():
    pdbpp.xpm(PdbTest)


def runpdb(func, input):
    oldstdin = sys.stdin
    oldstdout = sys.stdout
    oldstderr = sys.stderr
    # Use __dict__ to avoid class descriptor (staticmethod).
    old_get_terminal_size = pdbpp.Pdb.__dict__["get_terminal_size"]

    if sys.version_info < (3, ):
        text_type = unicode  # noqa: F821
    else:
        text_type = str

    class MyBytesIO(BytesIO):
        """write accepts unicode or bytes"""

        encoding = 'ascii'

        def __init__(self, encoding='utf-8'):
            self.encoding = encoding

        def write(self, msg):
            if isinstance(msg, text_type):
                msg = msg.encode(self.encoding)
            super(MyBytesIO, self).write(msg)

        def get_unicode_value(self):
            return self.getvalue().decode(self.encoding).replace(
                pdbpp.CLEARSCREEN, "<CLEARSCREEN>\n"
            ).replace(chr(27), "^[")

    # Use a predictable terminal size.
    pdbpp.Pdb.get_terminal_size = staticmethod(lambda: (80, 24))
    try:
        sys.stdin = FakeStdin(input)
        sys.stdout = stdout = MyBytesIO()
        sys.stderr = stderr = MyBytesIO()
        func()
    except InnerTestException:
        pass
    except bdb.BdbQuit:
        print("!! Received unexpected bdb.BdbQuit !!")
    except Exception:
        # Make it available for pytests output capturing.
        print(stdout.get_unicode_value(), file=oldstdout)
        raise
    finally:
        sys.stdin = oldstdin
        sys.stdout = oldstdout
        sys.stderr = oldstderr
        pdbpp.Pdb.get_terminal_size = old_get_terminal_size

    stderr = stderr.get_unicode_value()
    if stderr:
        # Make it available for pytests output capturing.
        print(stdout.get_unicode_value())
        raise AssertionError("Unexpected output on stderr: %s" % stderr)

    return stdout.get_unicode_value().splitlines()


def extract_commands(lines):
    cmds = []
    prompts = {'# ', '(#) ', '((#)) ', '(((#))) ', '(Pdb) '}
    for line in lines:
        for prompt in prompts:
            if line.startswith(prompt):
                cmds.append(line[len(prompt):])
                continue
    return cmds


shortcuts = [
    ('[', '\\['),
    (']', '\\]'),
    ('(', '\\('),
    (')', '\\)'),
    ('^', '\\^'),
    ('<COLORCURLINE>', r'\^\[\[44m\^\[\[36;01;44m *[0-9]+\^\[\[00;44m'),
    ('<COLORNUM>', r'\^\[\[36;01m *[0-9]+\^\[\[00m'),
    ('<COLORFNAME>', r'\^\[\[33;01m'),
    ('<COLORLNUM>', r'\^\[\[36;01m'),
    ('<COLORRESET>', r'\^\[\[00m'),
    ('NUM', ' *[0-9]+'),
]


def cook_regexp(s):
    for key, value in shortcuts:
        s = s.replace(key, value)
    return s


def run_func(func, expected):
    """Runs given function and returns its output along with expected patterns.

    It does not make any assertions. To compare func's output with expected
    lines, use `check` function.
    """
    expected = expected.strip().splitlines()
    # Remove comments.
    expected = [re.split(r'\s+###', line)[0] for line in expected]
    commands = extract_commands(expected)
    expected = list(map(cook_regexp, expected))
    return expected, runpdb(func, commands)


def count_frames():
    f = sys._getframe()
    i = 0
    while f is not None:
        i += 1
        f = f.f_back
    return i


class InnerTestException(Exception):
    """Ignored by check()."""
    pass


def check(func, expected):
    expected, lines = run_func(func, expected)
    if expected:
        maxlen = max(map(len, expected))
    else:
        maxlen = 0
    all_ok = True
    print()
    for pattern, string in zip_longest(expected, lines):
        if pattern is not None and string is not None:
            ok = re.match(pattern, string)
        else:
            ok = False
            if pattern is None:
                pattern = '<None>'
            if string is None:
                string = '<None>'
        # Use "$" to mark end of line with trailing space
        if re.search(r'\s+$', string):
            string += '$'
        if re.search(r'\s+$', pattern):
            pattern += '$'
        pattern = pattern.replace("\t", "\\t")
        string = string.replace("\t", "\\t")
        print(pattern.ljust(maxlen+1), '| ', string, end='')
        if ok:
            print()
        else:
            print(pdbpp.Color.set(pdbpp.Color.red, '    <<<<<'))
            all_ok = False
    assert all_ok


def test_prompt_setter():
    from pdb import Pdb

    p = Pdb()
    assert p.prompt == "(Pdb++) "

    p.prompt = "(Pdb)"
    assert p.prompt == "(Pdb++)"
    p.prompt = "ipdb> "
    assert p.prompt == "ipdb++> "
    p.prompt = "custom"
    assert p.prompt == "custom++"
    p.prompt = "custom "
    assert p.prompt == "custom++ "
    p.prompt = "custom :"
    assert p.prompt == "custom++ :"
    p.prompt = "custom  "
    assert p.prompt == "custom++  "
    p.prompt = ""
    assert p.prompt == ""
    # Not changed (also used in tests).
    p.prompt = "# "
    assert p.prompt == "# "
    # Can be forced.
    p._prompt = "custom"
    assert p.prompt == "custom"


def test_config_pygments(monkeypatch):
    from pdb import DefaultConfig, Pdb
    import pygments.formatters

    assert not hasattr(DefaultConfig, "use_terminal256formatter")

    p = Pdb(Config=DefaultConfig)

    monkeypatch.delenv("TERM", raising=False)
    assert isinstance(
        p._get_pygments_formatter(), pygments.formatters.TerminalFormatter
    )

    monkeypatch.setenv("TERM", "xterm-256color")
    assert isinstance(
        p._get_pygments_formatter(), pygments.formatters.Terminal256Formatter
    )

    monkeypatch.setenv("TERM", "xterm-kitty")
    assert isinstance(
        p._get_pygments_formatter(), pygments.formatters.TerminalTrueColorFormatter
    )

    class Config(DefaultConfig):
        formatter = object()

    assert Pdb(Config=Config)._get_pygments_formatter() is Config.formatter

    class Config(DefaultConfig):
        pygments_formatter_class = "pygments.formatters.TerminalTrueColorFormatter"

    assert isinstance(
        Pdb(Config=Config)._get_pygments_formatter(),
        pygments.formatters.TerminalTrueColorFormatter
    )

    source = Pdb(Config=Config).format_source("print(42)")
    assert source.startswith("\x1b[38;2;0;128;")
    assert "print\x1b[39" in source
    assert source.endswith("m42\x1b[39m)\n")


@pytest.mark.parametrize("use_pygments", (None, True, False))
def test_config_missing_pygments(use_pygments, monkeypatch_importerror):
    from pdb import DefaultConfig, Pdb

    class Config(DefaultConfig):
        pass

    Config.use_pygments = use_pygments

    class PdbForMessage(Pdb):
        messages = []

        def message(self, msg):
            self.messages.append(msg)

    pdb_ = PdbForMessage(Config=Config)

    with monkeypatch_importerror(('pygments', 'pygments.formatters')):
        with pytest.raises(ImportError):
            pdb_._get_pygments_formatter()
        assert pdb_._get_source_highlight_function() is False
        assert pdb_.format_source("print(42)") == "print(42)"

    if use_pygments is True:
        assert pdb_.messages == ['Could not import pygments, disabling.']
    else:
        assert pdb_.messages == []

    # Cover branch for cached _highlight property.
    assert pdb_.format_source("print(42)") == "print(42)"


def test_config_pygments_deprecated_use_terminal256formatter(monkeypatch):
    from pdb import DefaultConfig, Pdb
    import pygments.formatters

    monkeypatch.setenv("TERM", "xterm-256color")

    class Config(DefaultConfig):
        use_terminal256formatter = False
    assert isinstance(
        Pdb(Config=Config)._get_pygments_formatter(),
        pygments.formatters.TerminalFormatter
    )

    class Config(DefaultConfig):
        use_terminal256formatter = True
    assert isinstance(
        Pdb(Config=Config)._get_pygments_formatter(),
        pygments.formatters.Terminal256Formatter
    )


def test_runpdb():
    def fn():
        set_trace()
        a = 1
        b = 2
        c = 3
        return a+b+c

    check(fn, """
[NUM] > .*fn()
-> a = 1
   5 frames hidden .*
# n
[NUM] > .*fn()
-> b = 2
   5 frames hidden .*
# n
[NUM] > .*fn()
-> c = 3
   5 frames hidden .*
# c
""")


def test_set_trace_remembers_previous_state():
    def fn():
        a = 1
        set_trace()
        a = 2
        set_trace(cleanup=False)
        a = 3
        set_trace(cleanup=False)
        a = 4
        return a

    check(fn, """
[NUM] > .*fn()
-> a = 2
   5 frames hidden .*
# display a
# c
[NUM] > .*fn()
-> a = 3
   5 frames hidden .*
a: 1 --> 2
# c
[NUM] > .*fn()
-> a = 4
   5 frames hidden .*
a: 2 --> 3
# c
""")


def test_set_trace_remembers_previous_state_via_module():
    def fn():
        a = 1
        set_trace_via_module()
        a = 2
        set_trace_via_module(cleanup=False)
        a = 3
        set_trace_via_module(cleanup=False)
        a = 4
        return a

    check(fn, """
[NUM] > .*fn()
-> a = 2
   5 frames hidden .*
# display a
# c
[NUM] > .*fn()
-> a = 3
   5 frames hidden .*
a: 1 --> 2
# c
[NUM] > .*fn()
-> a = 4
   5 frames hidden .*
a: 2 --> 3
# c
""")


class TestPdbMeta:
    def test_called_for_set_trace_false(self):
        assert pdbpp.PdbMeta.called_for_set_trace(sys._getframe()) is False

    def test_called_for_set_trace_staticmethod(self):

        class Foo:
            @staticmethod
            def set_trace():
                frame = sys._getframe()
                assert pdbpp.PdbMeta.called_for_set_trace(frame) is frame
                return True

        assert Foo.set_trace() is True

    def test_called_for_set_trace_method(self):

        class Foo:
            def set_trace(self):
                frame = sys._getframe()
                assert pdbpp.PdbMeta.called_for_set_trace(frame) is frame
                return True

        assert Foo().set_trace() is True

    def test_called_for_set_trace_via_func(self):
        def set_trace():
            frame = sys._getframe()
            assert pdbpp.PdbMeta.called_for_set_trace(frame) is frame
            return True

        assert set_trace() is True

    def test_called_for_set_trace_via_other_func(self):

        def somefunc():
            def meta():
                frame = sys._getframe()
                assert pdbpp.PdbMeta.called_for_set_trace(frame) is False
            meta()
            return True

        assert somefunc() is True


def test_forget_with_new_pdb():
    """Regression test for having used local.GLOBAL_PDB in forget.

    This caused "AttributeError: 'NewPdb' object has no attribute 'lineno'",
    e.g. when pdbpp was used before pytest's debugging plugin was setup, which
    then later uses a custom Pdb wrapper.
    """
    def fn():
        set_trace()

        class NewPdb(PdbTest, pdbpp.Pdb):
            def set_trace(self, *args):
                print("new_set_trace")
                return super(NewPdb, self).set_trace(*args)

        new_pdb = NewPdb()
        new_pdb.set_trace()

    check(fn, """
[NUM] > .*fn()
-> class NewPdb(PdbTest, pdbpp.Pdb):
   5 frames hidden .*
# c
new_set_trace
--Return--
[NUM] .*set_trace()->None
-> return super(NewPdb, self).set_trace(\\*args)
   5 frames hidden .*
# l
NUM .*
NUM .*
NUM .*
NUM .*
NUM .*
NUM .*
NUM .*
NUM .*
NUM .*
NUM .*
NUM .*
# c
""")


def test_global_pdb_with_classmethod():
    def fn():
        set_trace()
        assert isinstance(pdbpp.local.GLOBAL_PDB, PdbTest)

        class NewPdb(PdbTest, pdbpp.Pdb):
            def set_trace(self, *args):
                print("new_set_trace")
                assert pdbpp.local.GLOBAL_PDB is self
                ret = super(NewPdb, self).set_trace(*args)
                assert pdbpp.local.GLOBAL_PDB is self
                return ret

        new_pdb = NewPdb()
        new_pdb.set_trace()

    check(fn, """
[NUM] > .*fn()
-> assert isinstance(pdbpp.local.GLOBAL_PDB, PdbTest)
   5 frames hidden .*
# c
new_set_trace
[NUM] .*set_trace()
-> assert pdbpp.local.GLOBAL_PDB is self
   5 frames hidden .*
# c
""")


def test_global_pdb_via_new_class_in_init_method():
    def fn():
        set_trace()
        first = pdbpp.local.GLOBAL_PDB
        assert isinstance(pdbpp.local.GLOBAL_PDB, PdbTest)

        class PdbLikePytest(object):
            @classmethod
            def init_pdb(cls):

                class NewPdb(PdbTest, pdbpp.Pdb):
                    def set_trace(self, frame):
                        print("new_set_trace")
                        super(NewPdb, self).set_trace(frame)

                return NewPdb()

            @classmethod
            def set_trace(cls, *args, **kwargs):
                frame = sys._getframe().f_back
                pdb_ = cls.init_pdb(*args, **kwargs)
                return pdb_.set_trace(frame)

        PdbLikePytest.set_trace()
        second = pdbpp.local.GLOBAL_PDB
        assert first != second

        PdbLikePytest.set_trace()
        third = pdbpp.local.GLOBAL_PDB
        assert third == second

    check(fn, """
[NUM] > .*fn()
-> first = pdbpp.local.GLOBAL_PDB
   5 frames hidden .*
# c
new_set_trace
[NUM] > .*fn()
-> second = pdbpp.local.GLOBAL_PDB
   5 frames hidden .*
# c
new_set_trace
[NUM] > .*fn()
-> third = pdbpp.local.GLOBAL_PDB
   5 frames hidden .*
# c
""")


def test_global_pdb_via_existing_class_in_init_method():
    def fn():
        set_trace()
        first = pdbpp.local.GLOBAL_PDB
        assert isinstance(pdbpp.local.GLOBAL_PDB, PdbTest)

        class NewPdb(PdbTest, pdbpp.Pdb):
            def set_trace(self, frame):
                print("new_set_trace")
                super(NewPdb, self).set_trace(frame)

        class PdbViaClassmethod(object):
            @classmethod
            def init_pdb(cls):
                return NewPdb()

            @classmethod
            def set_trace(cls, *args, **kwargs):
                frame = sys._getframe().f_back
                pdb_ = cls.init_pdb(*args, **kwargs)
                return pdb_.set_trace(frame)

        PdbViaClassmethod.set_trace()
        second = pdbpp.local.GLOBAL_PDB
        assert first != second

        PdbViaClassmethod.set_trace()
        third = pdbpp.local.GLOBAL_PDB
        assert third == second

    check(fn, """
[NUM] > .*fn()
-> first = pdbpp.local.GLOBAL_PDB
   5 frames hidden .*
# c
new_set_trace
[NUM] > .*fn()
-> second = pdbpp.local.GLOBAL_PDB
   5 frames hidden .*
# c
new_set_trace
[NUM] > .*fn()
-> third = pdbpp.local.GLOBAL_PDB
   5 frames hidden .*
# c
""")


def test_global_pdb_can_be_skipped():
    def fn():
        set_trace()
        first = pdbpp.local.GLOBAL_PDB
        assert isinstance(first, PdbTest)

        class NewPdb(PdbTest, pdbpp.Pdb):
            def set_trace(self, *args):
                print("new_set_trace")
                assert pdbpp.local.GLOBAL_PDB is not self
                ret = super(NewPdb, self).set_trace(*args)
                assert pdbpp.local.GLOBAL_PDB is not self
                return ret

        new_pdb = NewPdb(use_global_pdb=False)
        new_pdb.set_trace()
        assert pdbpp.local.GLOBAL_PDB is not new_pdb

        set_trace(cleanup=False)
        assert pdbpp.local.GLOBAL_PDB is not new_pdb

    check(fn, """
[NUM] > .*fn()
-> first = pdbpp.local.GLOBAL_PDB
   5 frames hidden .*
# c
new_set_trace
[NUM] .*set_trace()
-> assert pdbpp.local.GLOBAL_PDB is not self
   5 frames hidden .*
# readline_ = pdbpp.local.GLOBAL_PDB.fancycompleter.config.readline
# assert readline_.get_completer() != pdbpp.local.GLOBAL_PDB.complete
# c
[NUM] > .*fn()
-> assert pdbpp.local.GLOBAL_PDB is not new_pdb
   5 frames hidden .*
# c
""")


def test_global_pdb_can_be_skipped_unit(monkeypatch_pdb_methods):
    """Same as test_global_pdb_can_be_skipped, but with mocked Pdb methods."""
    def fn():
        set_trace()
        first = pdbpp.local.GLOBAL_PDB
        assert isinstance(first, PdbTest)

        class NewPdb(PdbTest, pdbpp.Pdb):
            def set_trace(self, *args):
                print("new_set_trace")
                assert pdbpp.local.GLOBAL_PDB is not self
                ret = super(NewPdb, self).set_trace(*args)
                assert pdbpp.local.GLOBAL_PDB is not self
                return ret

        new_pdb = NewPdb(use_global_pdb=False)
        new_pdb.set_trace()
        assert pdbpp.local.GLOBAL_PDB is not new_pdb

        set_trace(cleanup=False)
        assert pdbpp.local.GLOBAL_PDB is not new_pdb

    check(fn, """
=== set_trace
new_set_trace
=== set_trace
=== set_trace
""")


def test_global_pdb_can_be_skipped_but_set():
    def fn():
        set_trace()
        first = pdbpp.local.GLOBAL_PDB
        assert isinstance(first, PdbTest)

        class NewPdb(PdbTest, pdbpp.Pdb):
            def set_trace(self, *args):
                print("new_set_trace")
                assert pdbpp.local.GLOBAL_PDB is self
                ret = super(NewPdb, self).set_trace(*args)
                assert pdbpp.local.GLOBAL_PDB is self
                return ret

        new_pdb = NewPdb(use_global_pdb=False, set_global_pdb=True)
        new_pdb.set_trace()
        assert pdbpp.local.GLOBAL_PDB is new_pdb

        set_trace(cleanup=False)
        assert pdbpp.local.GLOBAL_PDB is new_pdb

    check(fn, """
[NUM] > .*fn()
-> first = pdbpp.local.GLOBAL_PDB
   5 frames hidden .*
# c
new_set_trace
[NUM] .*set_trace()
-> assert pdbpp.local.GLOBAL_PDB is self
   5 frames hidden .*
# readline_ = pdbpp.local.GLOBAL_PDB.fancycompleter.config.readline
# assert readline_.get_completer() == pdbpp.local.GLOBAL_PDB.complete
# c
new_set_trace
[NUM] > .*fn()
-> assert pdbpp.local.GLOBAL_PDB is new_pdb
   5 frames hidden .*
# c
""")


def test_global_pdb_can_be_skipped_but_set_unit(monkeypatch_pdb_methods):
    def fn():
        set_trace()
        first = pdbpp.local.GLOBAL_PDB
        assert isinstance(first, PdbTest)

        class NewPdb(PdbTest, pdbpp.Pdb):
            def set_trace(self, *args):
                print("new_set_trace")
                assert pdbpp.local.GLOBAL_PDB is self
                ret = super(NewPdb, self).set_trace(*args)
                assert pdbpp.local.GLOBAL_PDB is self
                return ret

        new_pdb = NewPdb(use_global_pdb=False, set_global_pdb=True)
        new_pdb.set_trace()
        assert pdbpp.local.GLOBAL_PDB is new_pdb

        set_trace(cleanup=False)
        assert pdbpp.local.GLOBAL_PDB is new_pdb

    check(fn, """
=== set_trace
new_set_trace
=== set_trace
new_set_trace
=== set_trace
""")


def test_global_pdb_only_reused_for_same_class(monkeypatch_pdb_methods):
    def fn():
        class NewPdb(PdbTest, pdbpp.Pdb):
            def set_trace(self, *args):
                print("new_set_trace")
                ret = super(NewPdb, self).set_trace(*args)
                return ret

        new_pdb = NewPdb()
        new_pdb.set_trace()
        assert pdbpp.local.GLOBAL_PDB is new_pdb

        set_trace(cleanup=False)
        assert pdbpp.local.GLOBAL_PDB is not new_pdb

        # What "debug" does, for coverage.
        new_pdb = NewPdb()
        new_pdb.set_trace()
        assert pdbpp.local.GLOBAL_PDB is new_pdb
        pdbpp.local.GLOBAL_PDB._use_global_pdb_for_class = PdbTest
        set_trace(cleanup=False)
        assert pdbpp.local.GLOBAL_PDB is new_pdb

        # Explicit kwarg for coverage.
        new_pdb = NewPdb(set_global_pdb=False)
        new_pdb.set_trace()
        assert pdbpp.local.GLOBAL_PDB is not new_pdb

    check(fn, """
new_set_trace
=== set_trace
=== set_trace
new_set_trace
=== set_trace
new_set_trace
=== set_trace
new_set_trace
=== set_trace
""")


def test_global_pdb_not_reused_with_different_home(
    monkeypatch_pdb_methods, monkeypatch
):
    def fn():
        set_trace()
        first = pdbpp.local.GLOBAL_PDB

        set_trace(cleanup=False)
        assert first is pdbpp.local.GLOBAL_PDB

        monkeypatch.setenv("HOME", "something else")
        set_trace(cleanup=False)
        assert first is not pdbpp.local.GLOBAL_PDB

    check(fn, """
=== set_trace
=== set_trace
=== set_trace
""")


def test_single_question_mark():
    def fn():
        def nodoc():
            pass

        def f2(x, y):
            """Return product of x and y"""
            return x * y
        set_trace()
        a = 1
        b = 2
        c = 3
        return a+b+c

    check(fn, r"""
[NUM] > .*fn()
-> a = 1
   5 frames hidden .*
# f2
<function .*f2 at .*>
# f2?
.*Type:.*function
.*String Form:.*<function .*f2 at .*>
^[[31;01mFile:^[[00m           {filename}:{lnum_f2}
.*Definition:.*f2(x, y)
.*Docstring:.*Return product of x and y
# nodoc?
.*Type:.*function
.*String Form:.*<function .*nodoc at .*>
^[[31;01mFile:^[[00m           {filename}:{lnum_nodoc}
^[[31;01mDefinition:^[[00m     nodoc()
# doesnotexist?
\*\*\* NameError.*
# c
    """.format(
        filename=RE_THIS_FILE_CANONICAL,
        lnum_nodoc=fn.__code__.co_firstlineno + 1,
        lnum_f2=fn.__code__.co_firstlineno + 4,
    ))


def test_double_question_mark():
    """Test do_inspect_with_source."""
    def fn():
        def f2(x, y):
            """Return product of x and y"""
            return x * y

        set_trace()
        a = 1
        b = 2
        c = 3
        return a+b+c

    check(fn, r"""
[NUM] > .*fn()
-> a = 1
   5 frames hidden .*
# f2
<function .*f2 at .*>
# f2??
.*Type:.*function
.*String Form:.*<function .*f2 at .*>
^[[31;01mFile:^[[00m           {filename}
.*Definition:.*f2(x, y)
.*Docstring:.*Return product of x and y
.*Source:.*
.* def f2(x, y):
.*     \"\"\"Return product of x and y\"\"\"
.*     return x \* y
# doesnotexist??
\*\*\* NameError.*
# c
    """.format(
        filename=RE_THIS_FILE_CANONICAL,
    ))


def test_question_mark_unit(capsys, LineMatcher):
    _pdb = PdbTest()
    _pdb.reset()

    foo = {12: 34}  # noqa: F841
    _pdb.setup(sys._getframe(), None)

    _pdb.do_inspect("foo")

    out, err = capsys.readouterr()
    LineMatcher(out.splitlines()).fnmatch_lines([
        "\x1b[31;01mString Form:\x1b[00m    {12: 34}"
    ])

    _pdb.do_inspect("doesnotexist")
    out, err = capsys.readouterr()
    LineMatcher(out.splitlines()).re_match_lines([
        r"^\*\*\* NameError:",
    ])

    # Source for function, indented docstring.
    def foo():
        """doc_for_foo

        3rd line."""
        raise NotImplementedError()
    _pdb.setup(sys._getframe(), None)
    _pdb.do_inspect_with_source("foo")
    out, err = capsys.readouterr()
    LineMatcher(out.splitlines()).re_match_lines([
        r"\x1b\[31;01mDocstring:\x1b\[00m      doc_for_foo",
        r"",
        r"                3rd line\.",
        r"\x1b\[31;01mSource:\x1b\[00m        ",
        r" ?\d+         def foo\(\):",
        r" ?\d+             raise NotImplementedError\(\)",
    ])

    # Missing source
    _pdb.do_inspect_with_source("str.strip")
    out, err = capsys.readouterr()
    LineMatcher(out.splitlines()).fnmatch_lines([
        "\x1b[31;01mSource:\x1b[00m         -",
    ])


def test_single_question_mark_with_existing_command(monkeypatch):
    def mocked_inspect(self, arg):
        print("mocked_inspect: %r" % arg)
    monkeypatch.setattr(PdbTest, "do_inspect", mocked_inspect)

    def fn():
        mp = monkeypatch  # noqa: F841

        class MyClass:
            pass
        a = MyClass()  # noqa: F841
        set_trace()

    check(fn, """
--Return--
[NUM] > .*fn()->None
-> set_trace()
   5 frames hidden .*
# a?
mocked_inspect: 'a'
# a.__class__?
mocked_inspect: 'a.__class__'
# !!a?
# !a?
do_shell_called: a?
\\*\\*\\* SyntaxError:
# mp.delattr(pdbpp.local.GLOBAL_PDB.__class__, "do_shell")
# !a?
\\*\\*\\* SyntaxError:
# help a
a(rgs)
.*
# c
""")


def test_up_local_vars():
    def nested():
        set_trace()
        return

    def fn():
        xx = 42  # noqa: F841
        nested()

    check(fn, """
[NUM] > .*nested()
-> return
   5 frames hidden .*
# up
[NUM] > .*fn()
-> nested()
# xx
42
# c
""")


def test_frame():
    def a():
        b()

    def b():
        c()

    def c():
        set_trace()
        return

    check(a, """
[NUM] > .*c()
-> return
   5 frames hidden .*
# f {frame_num_a}
[NUM] > .*a()
-> b()
# f
[{frame_num_a}] > .*a()
-> b()
# f 0
[ 0] > .*()
-> .*
# f -1
[{stack_len}] > .*c()
-> return
# c
    """.format(
        frame_num_a=count_frames() + 2 - 5,
        stack_len=len(traceback.extract_stack())
    ))


@pytest.mark.skipif(sys.version_info < (3, 6),
                    reason="only with f-strings")
def test_fstrings(monkeypatch):
    def mocked_inspect(self, arg):
        print("mocked_inspect: %r" % arg)
    monkeypatch.setattr(PdbTest, "do_inspect", mocked_inspect)

    def f():
        set_trace()

    check(f, """
--Return--
[NUM] > .*
-> set_trace()
   5 frames hidden .*
# f"fstring"
'fstring'
# f"foo"?
mocked_inspect: 'f"foo"'
# c
""")


def test_prefixed_strings(monkeypatch):
    def mocked_inspect(self, arg):
        print("mocked_inspect: %r" % arg)

    monkeypatch.setattr(PdbTest, "do_inspect", mocked_inspect)

    def f():
        set_trace()

    check(
        f,
        """
--Return--
[NUM] > .*
-> set_trace()
   5 frames hidden .*
# b"string"
{bytestring!r}
# u"string"
{unicodestring!r}
# r"string"
'string'
# b"foo"?
mocked_inspect: 'b"foo"'
# r"foo"?
mocked_inspect: 'r"foo"'
# u"foo"?
mocked_inspect: 'u"foo"'
# c
""".format(bytestring=b"string", unicodestring=u"string"))


def test_up_down_arg():
    def a():
        b()

    def b():
        c()

    def c():
        set_trace()
        return

    check(a, """
[NUM] > .*c()
-> return
   5 frames hidden .*
# up 3
[NUM] > .*runpdb()
-> func()
# down 1
[NUM] > .*a()
-> b()
# c
""")


def test_up_down_sticky():
    def a():
        b()

    def b():
        set_trace()
        return

    check(a, """
[NUM] > .*b()
-> return
   5 frames hidden .*
# sticky
<CLEARSCREEN>
[NUM] > .*b(), 5 frames hidden

NUM         def b():
NUM             set_trace()
NUM  ->         return
# up
<CLEARSCREEN>
[NUM] > .*a(), 5 frames hidden

NUM         def a()
NUM  ->         b()
# c
""")


def test_top_bottom():
    def a():
        b()

    def b():
        c()

    def c():
        set_trace()
        return

    check(
        a, """
[NUM] > .*c()
-> return
   5 frames hidden .*
# top
[ 0] > .*()
-> .*
# bottom
[{stack_len}] > .*c()
-> return
# c
""".format(stack_len=len(traceback.extract_stack())))


def test_top_bottom_frame_post_mortem():
    def fn():
        def throws():
            0 / 0

        def f():
            throws()

        try:
            f()
        except:
            pdbpp.post_mortem(Pdb=PdbTest)
    check(fn, r"""
[2] > .*throws()
-> 0 / 0
# top
[0] > .*fn()
-> f()
# top
\*\*\* Oldest frame
# bottom
[2] > .*throws()
-> 0 / 0
# bottom
\*\*\* Newest frame
# frame -1  ### Same as bottom, no error.
[2] > .*throws()
-> 0 / 0
# frame -2
[1] > .*f()
-> throws()
# frame -3
\*\*\* Out of range
# c
""")


def test_parseline():
    def fn():
        c = 42
        set_trace()
        return c

    check(fn, """
[NUM] > .*fn()
-> return c
   5 frames hidden .*
# c
42
# !c
do_shell_called: 'c'
42
# r = 5
# r
5
# r = 6
# r
6
# !!c
""")


def test_parseline_with_rc_commands(tmpdir, monkeypatch):
    """Test that parseline handles execution of rc lines during setup."""
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.delenv("USERPROFILE", raising=False)

    with tmpdir.as_cwd():
        with open(".pdbrc", "w") as f:
            f.writelines([
                "p 'readrc'\n",
                "alias myalias print(%1)\n",
            ])

        def fn():
            alias = "trigger"  # noqa: F841
            set_trace(readrc=True)

        check(fn, """
--Return--
'readrc'
[NUM] > .*fn()->None
-> set_trace(readrc=True)
   5 frames hidden .*
# alias myalias
myalias = print(%1)
# !!alias myalias
myalias = print(%1)
# myalias 42
42
# c
""")


def test_parseline_with_existing_command():
    def fn():
        c = 42
        debug = True  # noqa: F841

        class r:
            text = "r.text"

        set_trace()
        return c

    check(fn, """
[NUM] > .*fn()
-> return c
   5 frames hidden .*
# print(pdbpp.local.GLOBAL_PDB.parseline("foo = "))
('foo', '=', 'foo =')
# print(pdbpp.local.GLOBAL_PDB.parseline("c = "))
(None, None, 'c = ')
# print(pdbpp.local.GLOBAL_PDB.parseline("a = "))
(None, None, 'a = ')
# print(pdbpp.local.GLOBAL_PDB.parseline("list()"))
(None, None, 'list()')
# print(pdbpp.local.GLOBAL_PDB.parseline("next(my_iter)"))
(None, None, 'next(my_iter)')
# c
42
# debug print(1)
ENTERING RECURSIVE DEBUGGER
[1] > <string>(1)<module>()
(#) cont
1
LEAVING RECURSIVE DEBUGGER
# debug
True
# r.text
'r.text'
# cont
""")


def test_args_name():
    def fn():
        args = 42
        set_trace()
        return args

    check(fn, """
[NUM] > .*fn()
-> return args
   5 frames hidden .*
# args
42
# c
""")


def lineno():
    """Returns the current line number in our program."""
    return inspect.currentframe().f_back.f_lineno


@pytest.mark.parametrize("command,expected_regex", [
    ("", r"Documented commands \(type help <topic>\):"),
    ("EOF", "Handles the receipt of EOF as a command."),
    ("a", "Print the argument"),
    ("alias", "an alias"),
    ("args", "Print the argument"),
    ("b", "set a break"),
    ("break", "set a break"),
    ("bt", "Print a stack trace"),
    ("c", "Continue execution, only stop when a breakpoint"),
    ("cl", "clear all breaks"),
    ("clear", "clear all breaks"),
    ("commands", "Specify a list of commands for breakpoint"),
    ("condition", "must evaluate to true"),
    ("cont", "Continue execution, only stop when a breakpoint"),
    ("continue", "Continue execution, only stop when a breakpoint"),
    ("d", "Move the current frame .* down"),
    ("debug", "Enter a recursive debugger"),
    ("disable", "Disables the breakpoints"),
    ("display", "Add expression to the display list"),
    ("down", "Move the current frame .* down"),
    ("ed", "Open an editor"),
    ("edit", "Open an editor"),
    ("enable", "Enables the breakpoints"),
    ("exit", "Quit from the debugger."),
    ("h", "h(elp)"),
    ("help", "h(elp)"),
    ("hf_hide", "hide hidden frames"),
    ("hf_unhide", "unhide hidden frames"),
    ("ignore", "ignore count for the given breakpoint"),
    ("interact", "Start an interative interpreter"),
    ("j", "Set the next line that will be executed."),
    ("jump", "Set the next line that will be executed."),
    ("l", "List source code for the current file."),
    ("list", "List source code for the current file."),
    ("ll", "List source code for the current function."),
    ("longlist", "List source code for the current function."),
    ("n", "Continue execution until the next line"),
    ("next", "Continue execution until the next line"),
    ("p", "Print the value of the expression"),
    ("pp", "Pretty-print the value of the expression."),
    ("q", "Quit from the debugger."),
    ("quit", "Quit from the debugger."),
    ("r", "Continue execution until the current function returns."),
    ("restart", "Restart the debugged python program."),
    ("return", "Continue execution until the current function returns."),
    ("run", "Restart the debugged python program"),
    ("s", "Execute the current line, stop at the first possible occasion"),
    ("step", "Execute the current line, stop at the first possible occasion"),
    ("sticky", "Toggle sticky mode"),
    ("tbreak", "arguments as break"),
    ("track", "track expression"),
    ("u", "Move the current frame .* up"),
    ("unalias", "specified alias."),
    ("undisplay", "Remove expression from the display list"),
    ("unt", "until the line"),
    ("until", "until the line"),
    ("up", "Move the current frame .* up"),
    ("w", "Print a stack trace"),
    ("whatis", "Prints? the type of the argument."),
    ("where", "Print a stack trace"),
    ("hidden_frames", "Some frames might be marked as \"hidden\""),
    ("exec", r"Execute the \(one-line\) statement"),

    ("hf_list", r"\*\*\* No help"),
    ("paste", r"\*\*\* No help"),
    ("put", r"\*\*\* No help"),
    ("retval", r"\*\*\* No help|return value"),
    ("rv", r"\*\*\* No help|return value"),
    ("source", r"\*\*\* No help"),
    ("unknown_command", r"\*\*\* No help"),
    ("help", "print the list of available commands."),
])
def test_help(command, expected_regex):
    from pdb import StringIO
    instance = PdbTest()
    instance.stdout = StringIO()

    # Redirect sys.stdout because Python 2 pdb.py has `print >>self.stdout` for
    # some functions and plain ol' `print` for others.
    oldstdout = sys.stdout
    sys.stdout = instance.stdout
    try:
        instance.do_help(command)
    finally:
        sys.stdout = oldstdout

    output = instance.stdout.getvalue()
    assert re.search(expected_regex, output)


def test_shortlist():
    def fn():
        a = 1
        set_trace(Config=ConfigTest)
        return a

    check(fn, """
[NUM] > .*fn()
-> return a
   5 frames hidden .*
# l {line_num}, 3
NUM +\t    def fn():
NUM +\t        a = 1
NUM +\t        set_trace(Config=ConfigTest)
NUM +->	        return a
# c
""".format(line_num=fn.__code__.co_firstlineno))


def test_shortlist_with_pygments_and_EOF():
    def fn():
        a = 1
        set_trace(Config=ConfigWithPygments)
        return a

    check(fn, """
[NUM] > .*fn()
-> ^[[38;5;28;01mreturn^[[39;00m a
   5 frames hidden .*
# l {line_num}, 3
[EOF]
# c
""".format(line_num=100000))


def test_shortlist_with_highlight_and_EOF():
    def fn():
        a = 1
        set_trace(Config=ConfigWithHighlight)
        return a

    check(fn, """
[NUM] > .*fn()
-> return a
   5 frames hidden .*
# l {line_num}, 3
[EOF]
# c
""".format(line_num=100000))


@pytest.mark.parametrize('config', [ConfigWithPygments, ConfigWithPygmentsNone])
def test_shortlist_with_pygments(config):

    def fn():
        a = 1
        set_trace(Config=config)

        return a

    check(fn, """
[NUM] > .*fn()
-> ^[[38;5;28;01mreturn^[[39;00m a
   5 frames hidden .*
# l {line_num}, 5
NUM +\t$
NUM +\t    ^[[38;5;28;01mdef^[[39;00m ^[[38;5;21mfn^[[39m():
NUM +\t        a ^[[38;5;241m=^[[39m ^[[38;5;241m1^[[39m
NUM +\t        set_trace(Config^[[38;5;241m=^[[39mconfig)
NUM +\t$
NUM +->\t        ^[[38;5;28;01mreturn^[[39;00m a
# c
""".format(line_num=fn.__code__.co_firstlineno - 1))


def test_truncated_source_with_pygments():

    def fn():
        """some docstring longer than maxlength for truncate_long_lines, which is 80"""
        a = 1
        set_trace(Config=ConfigWithPygments)

        return a

    check(fn, """
[NUM] > .*fn()
-> ^[[38;5;28;01mreturn^[[39;00m a
   5 frames hidden .*
# l {line_num}, 5
NUM +\t$
NUM +\t    ^[[38;5;28;01mdef^[[39;00m ^[[38;5;21mfn^[[39m():
NUM +\t        ^[[38;5;124.*m\"\"\"some docstring longer than maxlength for truncate_long_lines, which is 80\"\"\"^[[39.*m
NUM +\t        a ^[[38;5;241m=^[[39m ^[[38;5;241m1^[[39m
NUM +\t        set_trace(Config^[[38;5;241m=^[[39mConfigWithPygments)
NUM +\t$
# sticky
<CLEARSCREEN>
[NUM] > .*fn(), 5 frames hidden

NUM +       ^[[38;5;28;01mdef^[[39;00m ^[[38;5;21mfn^[[39m():
NUM +           ^[[38;5;124.*m\"\"\"some docstring longer than maxlength for truncate_long_lines^[[39.*m
NUM +           a ^[[38;5;241m=^[[39m ^[[38;5;241m1^[[39m
NUM +           set_trace(Config^[[38;5;241m=^[[39mConfigWithPygments)
NUM +$
NUM +->         ^[[38;5;28;01mreturn^[[39;00m a
# c
""".format(line_num=fn.__code__.co_firstlineno - 1))  # noqa: E501


def test_truncated_source_with_pygments_and_highlight():

    def fn():
        """some docstring longer than maxlength for truncate_long_lines, which is 80"""
        a = 1
        set_trace(Config=ConfigWithPygmentsAndHighlight)

        return a

    check(fn, """
[NUM] > .*fn()
-> ^[[38;5;28;01mreturn^[[39;00m a
   5 frames hidden .*
# l {line_num}, 5
<COLORNUM> +\t$
<COLORNUM> +\t    ^[[38;5;28;01mdef^[[39;00m ^[[38;5;21mfn^[[39m():
<COLORNUM> +\t        ^[[38;5;124.*m\"\"\"some docstring longer than maxlength for truncate_long_lines, which is 80\"\"\"^[[39.*m
<COLORNUM> +\t        a ^[[38;5;241m=^[[39m ^[[38;5;241m1^[[39m
<COLORNUM> +\t        set_trace(Config^[[38;5;241m=^[[39mConfigWithPygmentsAndHighlight)
<COLORNUM> +\t$
# sticky
<CLEARSCREEN>
[NUM] > .*fn(), 5 frames hidden

<COLORNUM> +       ^[[38;5;28;01mdef^[[39;00m ^[[38;5;21mfn^[[39m():
<COLORNUM> +           ^[[38;5;124.*m\"\"\"some docstring longer than maxlength for truncate_long_lines^[[39.*m
<COLORNUM> +           a ^[[38;5;241m=^[[39m ^[[38;5;241m1^[[39m
<COLORNUM> +           set_trace(Config^[[38;5;241m=^[[39mConfigWithPygmentsAndHighlight)
<COLORNUM> +$
<COLORCURLINE> +->         ^[[38;5;28;01;44mreturn^[[39;00;44m a                                                       ^[[00m
# c
""".format(line_num=fn.__code__.co_firstlineno - 1))  # noqa: E501


def test_shortlist_with_highlight():

    def fn():
        a = 1
        set_trace(Config=ConfigWithHighlight)

        return a

    check(fn, """
[NUM] > .*fn()
-> return a
   5 frames hidden .*
# l {line_num}, 4
<COLORNUM> +\t    def fn():
<COLORNUM> +\t        a = 1
<COLORNUM> +\t        set_trace(Config=ConfigWithHighlight)
<COLORNUM> +\t$
<COLORNUM> +->\t        return a
# c
""".format(line_num=fn.__code__.co_firstlineno))


def test_shortlist_without_arg():
    """Ensure that forget was called for lineno."""
    def fn():
        a = 1
        set_trace(Config=ConfigTest)
        return a

    check(fn, """
[NUM] > .*fn()
-> return a
   5 frames hidden .*
# l
NUM \tdef test_shortlist_without_arg():
NUM \t    \"""Ensure that forget was called for lineno.\"""
.*
.*
.*
.*
.*
.*
.*
NUM \t-> return a
NUM \t   5 frames hidden .*
# c
""".format(line_num=fn.__code__.co_firstlineno))


def test_shortlist_heuristic():
    def fn():
        a = 1
        set_trace(Config=ConfigTest)
        return a

    check(fn, """
[NUM] > .*fn()
-> return a
   5 frames hidden .*
# list {line_num}, 3
NUM \t    def fn():
NUM \t        a = 1
NUM \t        set_trace(Config=ConfigTest)
NUM ->	        return a
# list(range(4))
[0, 1, 2, 3]
# c
""".format(line_num=fn.__code__.co_firstlineno))


def test_shortlist_with_second_set_trace_resets_lineno():
    def fn():
        def f1():
            set_trace(cleanup=False)

        set_trace()
        f1()

    check(fn, r"""
[NUM] > .*fn()
-> f1()
   5 frames hidden .*
# l {line_num}, 2
NUM \t    def fn():
NUM \t        def f1():
NUM \t            set_trace(cleanup=False)
# import pdb; pdbpp.local.GLOBAL_PDB.lineno
{set_lineno}
# c
--Return--
[NUM] > .*f1()->None
-> set_trace(cleanup=False)
   5 frames hidden .*
# import pdb; pdbpp.local.GLOBAL_PDB.lineno
# c
    """.format(
        line_num=fn.__code__.co_firstlineno,
        set_lineno=fn.__code__.co_firstlineno + 2,
    ))


def test_longlist():
    def fn():
        a = 1
        set_trace()
        return a

    check(fn, """
[NUM] > .*fn()
-> return a
   5 frames hidden .*
# ll
NUM         def fn():
NUM             a = 1
NUM             set_trace()
NUM  ->         return a
# c
""")


def test_longlist_with_highlight():
    def fn():
        a = 1
        set_trace(Config=ConfigWithHighlight)
        return a

    check(fn, r"""
[NUM] > .*fn()
-> return a
   5 frames hidden .*
# ll
<COLORNUM>         def fn():
<COLORNUM>             a = 1
<COLORNUM>             set_trace(Config=ConfigWithHighlight)
<COLORCURLINE> +->         return a                                                       ^[[00m$
# c
""")  # noqa: E501


def test_display():
    def fn():
        a = 1
        set_trace()
        b = 1  # noqa: F841
        a = 2
        a = 3
        return a

    check(fn, """
[NUM] > .*fn()
-> b = 1
   5 frames hidden .*
# display a
# n
[NUM] > .*fn()
-> a = 2
   5 frames hidden .*
# n
[NUM] > .*fn()
-> a = 3
   5 frames hidden .*
a: 1 --> 2
# undisplay a
# n
[NUM] > .*fn()
-> return a
   5 frames hidden .*
# c
""")


def test_display_undefined():
    def fn():
        set_trace()
        b = 42
        return b

    check(fn, """
[NUM] > .*fn()
-> b = 42
   5 frames hidden .*
# display b
# n
[NUM] > .*fn()
-> return b
   5 frames hidden .*
b: <undefined> --> 42
# c
""")


def test_sticky():
    def fn():
        set_trace()
        a = 1
        b = 2  # noqa: F841
        c = 3  # noqa: F841
        return a

    check(fn, """
[NUM] > .*fn()
-> a = 1
   5 frames hidden .*
# sticky
<CLEARSCREEN>
[NUM] > .*fn(), 5 frames hidden

NUM         def fn():
NUM             set_trace()
NUM  ->         a = 1
NUM             b = 2
NUM             c = 3
NUM             return a
# n
<CLEARSCREEN>
[NUM] > .*fn(), 5 frames hidden

NUM         def fn():
NUM             set_trace()
NUM             a = 1
NUM  ->         b = 2
NUM             c = 3
NUM             return a
# sticky
# n
[NUM] > .*fn()
-> c = 3
   5 frames hidden .*
# c
""")


def test_sticky_range():
    def fn():
        set_trace()
        a = 1
        b = 2  # noqa: F841
        c = 3  # noqa: F841
        return a
    _, lineno = inspect.getsourcelines(fn)
    start = lineno + 1
    end = lineno + 3

    check(fn, """
[NUM] > .*fn()
-> a = 1
   5 frames hidden .*
# sticky %d %d
<CLEARSCREEN>
[NUM] > .*fn(), 5 frames hidden

%d \\s+         set_trace()
NUM  ->         a = 1
NUM             b = 2
# c
""" % (start, end, start))


def test_sticky_by_default():
    class MyConfig(ConfigTest):
        sticky_by_default = True

    def fn():
        set_trace(Config=MyConfig)
        a = 1
        b = 2  # noqa: F841
        c = 3  # noqa: F841
        return a

    check(fn, """
[NUM] > .*fn(), 5 frames hidden

NUM         def fn():
NUM             set_trace(Config=MyConfig)
NUM  ->         a = 1
NUM             b = 2
NUM             c = 3
NUM             return a
# n
<CLEARSCREEN>
[NUM] > .*fn(), 5 frames hidden

NUM         def fn():
NUM             set_trace(Config=MyConfig)
NUM             a = 1
NUM  ->         b = 2
NUM             c = 3
NUM             return a
# c
""")


def test_sticky_by_default_with_use_pygments_auto():
    class MyConfig(ConfigTest):
        sticky_by_default = True
        use_pygments = None

    def fn():
        set_trace(Config=MyConfig)
        a = 1
        return a

    check(fn, """
[NUM] > .*fn(), 5 frames hidden

NUM         ^[[38;5;28;01mdef^[[39;00m ^[[38;5;21mfn^[[39m():
NUM             set_trace(Config^[[38;5;241m=^[[39mMyConfig)
NUM  ->         a ^[[38;5;241m=^[[39m ^[[38;5;241m1^[[39m
NUM             ^[[38;5;28;01mreturn^[[39;00m a
# c
""")


def test_sticky_dunder_exception():
    """Test __exception__ being displayed in sticky mode."""

    def fn():
        def raises():
            raise InnerTestException()

        set_trace()
        raises()

    check(fn, """
[NUM] > .*fn()
-> raises()
   5 frames hidden (try 'help hidden_frames')
# n
.*InnerTestException.*  ### via pdbpp.Pdb.user_exception (differs on py3/py27)
[NUM] > .*fn()
-> raises()
   5 frames hidden .*
# sticky
<CLEARSCREEN>
[NUM] > {filename}(NUM)fn(), 5 frames hidden

NUM         def fn():
NUM             def raises():
NUM                 raise InnerTestException()
NUM
NUM             set_trace(.*)
NUM  ->         raises()
InnerTestException:
# c
    """.format(
        filename=RE_THIS_FILE_CANONICAL,
    ))


def test_sticky_dunder_exception_with_highlight():
    """Test __exception__ being displayed in sticky mode."""

    def fn():
        def raises():
            raise InnerTestException()

        set_trace(Config=ConfigWithHighlight)
        raises()

    check(fn, """
[NUM] > .*fn()
-> raises()
   5 frames hidden (try 'help hidden_frames')
# n
.*InnerTestException.*  ### via pdbpp.Pdb.user_exception (differs on py3/py27)
[NUM] > .*fn()
-> raises()
   5 frames hidden .*
# sticky
<CLEARSCREEN>
[NUM] > <COLORFNAME>{filename}<COLORRESET>(<COLORNUM>)fn(), 5 frames hidden

<COLORNUM>         def fn():
<COLORNUM>             def raises():
<COLORNUM>                 raise InnerTestException()
<COLORNUM>
<COLORNUM>             set_trace(.*)
<COLORCURLINE>  ->         raises().*
<COLORLNUM>InnerTestException: <COLORRESET>
# c
    """.format(
        filename=RE_THIS_FILE_CANONICAL,
    ))


def test_format_exc_for_sticky():
    _pdb = PdbTest()
    f = _pdb._format_exc_for_sticky

    assert f((Exception, Exception())) == "Exception: "

    exc_from_str = Exception("exc_from_str")

    class UnprintableExc:
        def __str__(self):
            raise exc_from_str

    assert f((UnprintableExc, UnprintableExc())) == (
        "UnprintableExc: (unprintable exception: %r)" % exc_from_str
    )

    class UnprintableExc:
        def __str__(self):
            class RaisesInRepr(Exception):
                def __repr__(self):
                    raise Exception()
            raise RaisesInRepr()

    assert f((UnprintableExc, UnprintableExc())) == (
        "UnprintableExc: (unprintable exception)"
    )

    assert f((1, 3, 3)) == 'pdbpp: got unexpected __exception__: (1, 3, 3)'


def test_sticky_dunder_return():
    """Test __return__ being displayed in sticky mode."""

    def fn():
        def returns():
            return 40 + 2

        set_trace()
        returns()

    if sys.version_info < (3,):
        py27_return = "--Return--\n"
    else:
        py27_return = ""
    check(fn, """
[NUM] > .*fn()
-> returns()
   5 frames hidden (try 'help hidden_frames')
# s
--Call--
[NUM] > .*returns()
-> def returns()
   5 frames hidden .*
# sticky
<CLEARSCREEN>
[NUM] > .*(NUM)returns(), 5 frames hidden

NUM  ->         def returns():
NUM                 return 40 \\+ 2
# retval
\\*\\*\\* Not yet returned!
# r
<CLEARSCREEN>
""" + py27_return + """[NUM] > {filename}(NUM)returns()->42, 5 frames hidden

NUM             def returns():
NUM  ->             return 40 \\+ 2
 return 42
# retval
42
# c
    """.format(
        filename=RE_THIS_FILE_CANONICAL,
    ))


def test_sticky_with_user_exception():
    def fn():
        def throws():
            raise InnerTestException()

        set_trace()
        throws()

    if sys.version_info < (3,):
        py27_exc = "InnerTestException: InnerTestException()\n"
    else:
        py27_exc = ""
    check(fn, """
[NUM] > .*fn()
-> throws()
   5 frames hidden (try 'help hidden_frames')
# s
--Call--
[NUM] > .*throws()
-> def throws():
   5 frames hidden .*
# sticky
<CLEARSCREEN>
[NUM] > .*throws(), 5 frames hidden

NUM  ->         def throws():
NUM                 raise InnerTestException()
# n
<CLEARSCREEN>
[NUM] > .*throws(), 5 frames hidden

NUM             def throws():
NUM  ->             raise InnerTestException()
# n
<CLEARSCREEN>
""" + py27_exc + """[NUM] > .*throws(), 5 frames hidden

NUM             def throws():
NUM  ->             raise InnerTestException()
InnerTestException:
# c
""")


def test_sticky_dunder_return_with_highlight():
    class Config(ConfigWithHighlight, ConfigWithPygments):
        pass

    def fn():
        def returns():
            return 40 + 2

        set_trace(Config=Config)
        returns()

    expected, lines = run_func(fn, '# s\n# sticky\n# r\n# retval\n# c')
    assert lines[-4:] == [
        '^[[36;01m return 42^[[00m',
        '# retval',
        '42',
        '# c',
    ]

    colored_cur_lines = [
        x for x in lines
        if x.startswith('^[[44m^[[36;01;44m') and '->' in x
    ]
    assert len(colored_cur_lines) == 2


def test_exception_lineno():
    def bar():
        assert False

    def fn():
        try:
            a = 1  # noqa: F841
            bar()
            b = 2  # noqa: F841
        except AssertionError:
            xpm()

    check(fn, """
Traceback (most recent call last):
  File "{filename}", line NUM, in fn
    bar()
  File "{filename}", line NUM, in bar
    assert False
AssertionError.*

[NUM] > .*bar()
-> assert False
# u
[NUM] > .*fn()
-> bar()
# ll
NUM         def fn():
NUM             try:
NUM                 a = 1
NUM  >>             bar()
NUM                 b = 2
NUM             except AssertionError:
NUM  ->             xpm()
# c
    """.format(
        filename=RE_THIS_FILE,
    ))


def test_postmortem_noargs():

    def fn():
        try:
            a = 1  # noqa: F841
            1/0
        except ZeroDivisionError:
            pdbpp.post_mortem(Pdb=PdbTest)

    check(fn, """
[NUM] > .*fn()
-> 1/0
# c
""")


def test_postmortem_needs_exceptioncontext():
    pytest.raises(ValueError, pdbpp.post_mortem)


def test_exception_through_generator():
    def gen():
        yield 5
        assert False

    def fn():
        try:
            for i in gen():
                pass
        except AssertionError:
            xpm()

    check(fn, """
Traceback (most recent call last):
  File "{filename}", line NUM, in fn
    for i in gen():
  File "{filename}", line NUM, in gen
    assert False
AssertionError.*

[NUM] > .*gen()
-> assert False
# u
[NUM] > .*fn()
-> for i in gen():
# c
    """.format(
        filename=RE_THIS_FILE,
    ))


def test_py_code_source():  # noqa: F821
    src = py.code.Source("""
    def fn():
        x = 42
        set_trace()
        return x
    """)

    exec(src.compile(), globals())
    check(fn,  # noqa: F821
          """
[NUM] > .*fn()
-> return x
   5 frames hidden .*
# ll
NUM     def fn():
NUM         x = 42
NUM         set_trace()
NUM  ->     return x
# c
""")


def test_source():
    def bar():
        return 42

    def fn():
        set_trace()
        return bar()

    check(fn, """
[NUM] > .*fn()
-> return bar()
   5 frames hidden .*
# source bar
NUM         def bar():
NUM             return 42
# c
""")


def test_source_with_pygments():
    def bar():

        return 42

    def fn():
        set_trace(Config=ConfigWithPygments)
        return bar()

    check(fn, """
[NUM] > .*fn()
-> ^[[38;5;28;01mreturn^[[39;00m bar()
   5 frames hidden .*
# source bar
NUM         ^[[38;5;28;01mdef^[[39;00m ^[[38;5;21mbar^[[39m():
NUM
NUM             ^[[38;5;28;01mreturn^[[39;00m ^[[38;5;241m42^[[39m
# c
""")


def test_source_with_highlight():
    def bar():

        return 42

    def fn():
        set_trace(Config=ConfigWithHighlight)
        return bar()

    check(fn, """
[NUM] > .*fn()
-> return bar()
   5 frames hidden .*
# source bar
<COLORNUM>         def bar():
<COLORNUM>
<COLORNUM>             return 42
# c
""")


def test_source_with_pygments_and_highlight():
    def bar():

        return 42

    def fn():
        set_trace(Config=ConfigWithPygmentsAndHighlight)
        return bar()

    check(fn, """
[NUM] > .*fn()
-> ^[[38;5;28;01mreturn^[[39;00m bar()
   5 frames hidden .*
# source bar
<COLORNUM>         ^[[38;5;28;01mdef^[[39;00m ^[[38;5;21mbar^[[39m():
<COLORNUM>
<COLORNUM>             ^[[38;5;28;01mreturn^[[39;00m ^[[38;5;241m42^[[39m
# c
""")


def test_bad_source():
    def fn():
        set_trace()
        return 42

    check(fn, r"""
[NUM] > .*fn()
-> return 42
   5 frames hidden .*
# source 42
\*\* Error: .*module, class, method, function, traceback, frame, or code object .*\*\*
# c
""")  # noqa: E501


def test_edit():
    def fn():
        set_trace()
        return 42

    def bar():
        fn()
        return 100

    _, lineno = inspect.getsourcelines(fn)
    return42_lineno = lineno + 2
    call_fn_lineno = lineno + 5

    check(fn, r"""
[NUM] > .*fn()
-> return 42
   5 frames hidden .*
# edit
RUN emacs \+%d %s
# c
""" % (return42_lineno, RE_THIS_FILE_QUOTED))

    check(bar, r"""
[NUM] > .*fn()
-> return 42
   5 frames hidden .*
# up
[NUM] > .*bar()
-> fn()
# edit
RUN emacs \+%d %s
# c
""" % (call_fn_lineno, RE_THIS_FILE_QUOTED))


def test_edit_obj():
    def fn():
        bar()
        set_trace()
        return 42

    def bar():
        pass
    _, bar_lineno = inspect.getsourcelines(bar)

    check(fn, r"""
[NUM] > .*fn()
-> return 42
   5 frames hidden .*
# edit bar
RUN emacs \+%d %s
# c
""" % (bar_lineno, RE_THIS_FILE_CANONICAL_QUOTED))


def test_edit_py_code_source():
    src = py.code.Source("""
    def bar():
        set_trace()
        return 42
    """)
    _, base_lineno = inspect.getsourcelines(test_edit_py_code_source)
    dic = {'set_trace': set_trace}
    exec(src.compile(), dic)  # 8th line from the beginning of the function
    bar = dic['bar']
    src_compile_lineno = base_lineno + 8

    check(bar, r"""
[NUM] > .*bar()
-> return 42
   5 frames hidden .*
# edit bar
RUN emacs \+%d %s
# c
""" % (src_compile_lineno, RE_THIS_FILE_CANONICAL_QUOTED))


def test_put():
    def fn():
        set_trace()
        return 42
    _, lineno = inspect.getsourcelines(fn)
    start_lineno = lineno + 1

    check(fn, r"""
[NUM] > .*fn()
-> return 42
   5 frames hidden .*
# x = 10
# y = 12
# put
RUN epaste \+%d
'        x = 10\\n        y = 12\\n'
# c
""" % start_lineno)


def test_paste():
    def g():
        print('hello world')

    def fn():
        set_trace()
        if 4 != 5:
            g()
        return 42
    _, lineno = inspect.getsourcelines(fn)
    start_lineno = lineno + 1

    check(fn, r"""
[NUM] > .*fn()
-> if 4 != 5:
   5 frames hidden .*
# g()
hello world
# paste g()
hello world
RUN epaste \+%d
'hello world\\n'
# c
hello world
""" % start_lineno)


def test_put_if():
    def fn():
        x = 0
        if x < 10:
            set_trace()
        return x
    _, lineno = inspect.getsourcelines(fn)
    start_lineno = lineno + 3

    check(fn, r"""
[NUM] > .*fn()
-> return x
   5 frames hidden .*
# x = 10
# y = 12
# put
RUN epaste \+%d
.*x = 10\\n            y = 12\\n.
# c
""" % start_lineno)


def test_side_effects_free():
    r = pdbpp.side_effects_free
    assert r.match('  x')
    assert r.match('x.y[12]')
    assert not r.match('x(10)')
    assert not r.match('  x = 10')
    assert not r.match('x = 10')


def test_put_side_effects_free():
    def fn():
        x = 10  # noqa: F841
        set_trace()
        return 42
    _, lineno = inspect.getsourcelines(fn)
    start_lineno = lineno + 2

    check(fn, r"""
[NUM] > .*fn()
-> return 42
   5 frames hidden .*
# x
10
# x.__add__
.*
# y = 12
# put
RUN epaste \+%d
'        y = 12\\n'
# c
""" % start_lineno)


def test_enable_disable_via_module():
    def fn():
        x = 1
        pdbpp.disable()
        set_trace_via_module()
        x = 2
        pdbpp.enable()
        set_trace_via_module()
        return x

    check(fn, """
[NUM] > .*fn()
-> return x
   5 frames hidden .*
# x
2
# c
""")


def test_enable_disable_from_prompt_via_class():
    def fn():
        pdb_ = PdbTest()

        pdb_.set_trace()
        x = 1
        pdb_.set_trace()
        x = 2
        pdbpp.enable()
        pdb_.set_trace()
        return x

    check(fn, """
[NUM] > .*fn()
-> x = 1
   5 frames hidden .*
# pdbpp.disable()
# c
[NUM] > .*fn()
-> return x
   5 frames hidden .*
# x
2
# c
""")


def test_hideframe():
    @pdbpp.hideframe
    def g():
        pass
    assert g.__code__.co_consts[-1] is pdbpp._HIDE_FRAME


def test_hide_hidden_frames():
    @pdbpp.hideframe
    def g():
        set_trace()
        return 'foo'

    def fn():
        g()
        return 1

    check(fn, """
[NUM] > .*fn()
-> g()
   6 frames hidden .*
# down
... Newest frame
# hf_unhide
# down
[NUM] > .*g()
-> return 'foo'
# up
[NUM] > .*fn()
-> g()
# hf_hide        ### hide the frame again
# down
... Newest frame
# c
""")


def test_hide_current_frame():
    @pdbpp.hideframe
    def g():
        set_trace()
        return 'foo'

    def fn():
        g()
        return 1

    check(fn, """
[NUM] > .*fn()
-> g()
   6 frames hidden .*
# hf_unhide
# down           ### now the frame is no longer hidden
[NUM] > .*g()
-> return 'foo'
# hf_hide        ### hide the current frame, go to the top of the stack
[NUM] > .*fn()
-> g()
# c
""")


def test_hide_frame_for_set_trace_on_class():
    def g():
        # Simulate set_trace, with frame=None.
        pdbpp.cleanup()
        _pdb = PdbTest()
        _pdb.set_trace()
        return 'foo'

    def fn():
        g()
        return 1

    check(fn, """
[NUM] > .*g()
-> return 'foo'
   5 frames hidden .*
# hf_unhide
# down
\\*\\*\\* Newest frame
# c
""")


def test_list_hidden_frames():
    @pdbpp.hideframe
    def g():
        set_trace()
        return 'foo'

    @pdbpp.hideframe
    def k():
        return g()

    def fn():
        k()
        return 1
    check(fn, r"""
[NUM] > .*fn()
-> k()
   7 frames hidden .*
# hf_list
.*_multicall()
-> res = hook_impl.function(\*args)
.*_multicall()
-> res = hook_impl.function(\*args)
.*_multicall()
-> res = hook_impl.function(\*args)
.*_multicall()
-> res = hook_impl.function(\*args)
.*_multicall()
-> res = hook_impl.function(\*args)
.*k()
-> return g()
.*g()
-> return 'foo'
# c
""")


def test_hidden_pytest_frames():
    def s():
        __tracebackhide__ = True  # Ignored for set_trace in here.
        set_trace()
        return 'foo'

    def g(s=s):
        __tracebackhide__ = True
        return s()

    def k(g=g):
        return g()
    k = pdbpp.rebind_globals(k, {'__tracebackhide__': True})

    def fn():
        k()
        return 1

    check(fn, r"""
[NUM] > .*s()
-> return 'foo'
   7 frames hidden .*
# hf_list
.*_multicall()
-> res = hook_impl.function(\*args)
.*_multicall()
-> res = hook_impl.function(\*args)
.*_multicall()
-> res = hook_impl.function(\*args)
.*_multicall()
-> res = hook_impl.function(\*args)
.*_multicall()
-> res = hook_impl.function(\*args)
.*k()
-> return g()
.*g()
-> return s()
# c
    """)


def test_hidden_unittest_frames():

    def s(set_trace=set_trace):
        set_trace()
        return 'foo'

    def g(s=s):
        return s()
    g = pdbpp.rebind_globals(g, {'__unittest': True})

    def fn():
        return g()

    check(fn, r"""
[NUM] > .*s()
-> return 'foo'
   6 frames hidden .*
# hf_list
.*_multicall()
-> res = hook_impl.function(\*args)
.*_multicall()
-> res = hook_impl.function(\*args)
.*_multicall()
-> res = hook_impl.function(\*args)
.*_multicall()
-> res = hook_impl.function(\*args)
.*_multicall()
-> res = hook_impl.function(\*args)
.*g()
-> return s()
# c
    """)


def test_dont_show_hidden_frames_count():
    class MyConfig(ConfigTest):
        show_hidden_frames_count = False

    @pdbpp.hideframe
    def g():
        set_trace(Config=MyConfig)
        return 'foo'

    def fn():
        g()
        return 1

    check(fn, """
[NUM] > .*fn()
-> g()
# c           ### note that the hidden frame count is not displayed
""")


def test_disable_hidden_frames():
    class MyConfig(ConfigTest):
        enable_hidden_frames = False

    @pdbpp.hideframe
    def g():
        set_trace(Config=MyConfig)
        return 'foo'

    def fn():
        g()
        return 1

    check(fn, """
[NUM] > .*g()
-> return 'foo'
# c           ### note that we were inside g()
""")


def test_break_on_setattr():
    # we don't use a class decorator to keep 2.5 compatibility
    class Foo(object):
        pass
    Foo = pdbpp.break_on_setattr('x', Pdb=PdbTest)(Foo)

    def fn():
        obj = Foo()
        obj.x = 0
        return obj.x

    check(fn, """
[NUM] > .*fn()
-> obj.x = 0
   5 frames hidden .*
# hasattr(obj, 'x')
False
# n
[NUM] > .*fn()
-> return obj.x
   5 frames hidden .*
# p obj.x
0
# c
""")


def test_break_on_setattr_without_hidden_frames():

    class PdbWithConfig(PdbTest):
        def __init__(self, *args, **kwargs):
            class Config(ConfigTest):
                enable_hidden_frames = False

            super(PdbWithConfig, self).__init__(*args, Config=Config, **kwargs)

    class Foo(object):
        pass
    Foo = pdbpp.break_on_setattr('x', Pdb=PdbWithConfig)(Foo)

    def fn():
        obj = Foo()
        obj.x = 0
        return obj.x

    check(fn, """
[NUM] > .*fn()
-> obj.x = 0
# hasattr(obj, 'x')
False
# n
[NUM] > .*fn()
-> return obj.x
# p obj.x
0
# c
""")


def test_break_on_setattr_condition():
    def mycond(obj, value):
        return value == 42

    class Foo(object):
        pass
    # we don't use a class decorator to keep 2.5 compatibility
    Foo = pdbpp.break_on_setattr('x', condition=mycond, Pdb=PdbTest)(Foo)

    def fn():
        obj = Foo()
        obj.x = 0
        obj.x = 42
        return obj.x

    check(fn, """
[NUM] > .*fn()
-> obj.x = 42
   5 frames hidden .*
# obj.x
0
# n
[NUM] > .*fn()
-> return obj.x
   5 frames hidden .*
# obj.x
42
# c
""")


def test_break_on_setattr_non_decorator():
    class Foo(object):
        pass

    def fn():
        a = Foo()
        b = Foo()

        def break_if_a(obj, value):
            return obj is a

        pdbpp.break_on_setattr("bar", condition=break_if_a, Pdb=PdbTest)(Foo)
        b.bar = 10
        a.bar = 42

    check(fn, """
[NUM] > .*fn()
-> a.bar = 42
   5 frames hidden .*
# c
""")


def test_break_on_setattr_overridden():
    # we don't use a class decorator to keep 2.5 compatibility
    class Foo(object):
        def __setattr__(self, attr, value):
            object.__setattr__(self, attr, value+1)
    Foo = pdbpp.break_on_setattr('x', Pdb=PdbTest)(Foo)

    def fn():
        obj = Foo()
        obj.y = 41
        obj.x = 0
        return obj.x

    check(fn, """
[NUM] > .*fn()
-> obj.x = 0
   5 frames hidden .*
# obj.y
42
# hasattr(obj, 'x')
False
# n
[NUM] > .*fn()
-> return obj.x
   5 frames hidden .*
# p obj.x
1
# c
""")


def test_track_with_no_args():
    pytest.importorskip('rpython.translator.tool.reftracker')

    def fn():
        set_trace()
        return 42

    check(fn, """
[NUM] > .*fn()
-> return 42
# track
... SyntaxError:
# c
""")


def test_utf8():
    def fn():
        # тест
        a = 1
        set_trace(Config=ConfigWithHighlight)
        return a

    # we cannot easily use "check" because the output is full of ANSI escape
    # sequences
    expected, lines = run_func(fn, '# ll\n# c')
    assert u'тест' in lines[5]


def test_debug_normal():
    def g():
        a = 1
        return a

    def fn():
        g()
        set_trace()
        return 1

    check(fn, """
[NUM] > .*fn()
-> return 1
   5 frames hidden .*
# debug g()
ENTERING RECURSIVE DEBUGGER
[NUM] > .*
(#) s
--Call--
[NUM] > .*g()
-> def g():
(#) ll
NUM  ->     def g():
NUM             a = 1
NUM             return a
(#) c
LEAVING RECURSIVE DEBUGGER
# c
""")


def test_debug_thrice():
    def fn():
        set_trace()

    check(fn, """
--Return--
[NUM] > .*fn()
-> set_trace()
   5 frames hidden .*
# debug 1
ENTERING RECURSIVE DEBUGGER
[NUM] > .*
(#) debug 2
ENTERING RECURSIVE DEBUGGER
[NUM] > .*
((#)) debug 34
ENTERING RECURSIVE DEBUGGER
[NUM] > .*
(((#))) p 42
42
(((#))) c
LEAVING RECURSIVE DEBUGGER
((#)) c
LEAVING RECURSIVE DEBUGGER
(#) c
LEAVING RECURSIVE DEBUGGER
# c
""")


def test_syntaxerror_in_command():
    expected_debug_err = "ENTERING RECURSIVE DEBUGGER\n\\*\\*\\* SyntaxError: .*"

    # Python 3.8.0a2+ handles the SyntaxError itself.
    # Ref/followup: https://github.com/python/cpython/pull/12103
    # https://github.com/python/cpython/commit/3e93643
    if sys.version_info >= (3, 7, 3):
        expected_debug_err += "\nLEAVING RECURSIVE DEBUGGER"

    def f():
        set_trace()

    check(f, """
--Return--
[NUM] > .*f()
-> set_trace
   5 frames hidden .*
# print(
\\*\\*\\* SyntaxError: .*
# debug print(
%s
# c
""" % expected_debug_err)


def test_debug_with_overridden_continue():
    class CustomPdb(PdbTest, object):
        """CustomPdb that overrides do_continue like with pytest's wrapper."""

        def do_continue(self, arg):
            global count_continue
            count_continue += 1
            print("do_continue_%d" % count_continue)
            return super(CustomPdb, self).do_continue(arg)

        do_c = do_cont = do_continue

    def g():
        a = 1
        return a

    def fn():
        global count_continue
        count_continue = 0

        g()

        set_trace(Pdb=CustomPdb)
        set_trace(Pdb=CustomPdb)

        assert count_continue == 3
        return 1

    check(fn, """
[NUM] > .*fn()
-> set_trace(Pdb=CustomPdb)
   5 frames hidden .*
# c
do_continue_1
[NUM] > .*fn()
-> assert count_continue == 3
   5 frames hidden .*
# debug g()
ENTERING RECURSIVE DEBUGGER
[NUM] > .*
(#) s
--Call--
[NUM] > .*g()
-> def g():
(#) ll
NUM  ->     def g():
NUM             a = 1
NUM             return a
(#) c
do_continue_2
LEAVING RECURSIVE DEBUGGER
# c
do_continue_3
""")


def test_before_interaction_hook():
    class MyConfig(ConfigTest):
        def before_interaction_hook(self, pdb):
            pdb.stdout.write('HOOK!\n')

    def fn():
        set_trace(Config=MyConfig)
        return 1

    check(fn, """
[NUM] > .*fn()
-> return 1
   5 frames hidden .*
HOOK!
# c
""")


def test_unicode_bug():
    def fn():
        set_trace()
        x = "this is plain ascii"  # noqa: F841
        y = "this contains a unicode: à"  # noqa: F841
        return

    check_output = """
[NUM] > .*fn()
-> x = "this is plain ascii"
   5 frames hidden .*
# n
[NUM] > .*fn()
-> y = "this contains a unicode: à"
   5 frames hidden .*
# c
"""

    if sys.version_info < (3, ):
        check_output = check_output.decode('utf-8')

    check(fn, check_output)


def test_continue_arg():
    def fn():
        set_trace()
        x = 1
        y = 2
        z = 3
        return x+y+z
    _, lineno = inspect.getsourcelines(fn)
    line_z = lineno+4

    check(fn, """
[NUM] > .*fn()
-> x = 1
   5 frames hidden .*
# c {break_lnum}
Breakpoint NUM at {filename}:{break_lnum}
Deleted breakpoint NUM
[NUM] > .*fn()
-> z = 3
   5 frames hidden .*
# c
    """.format(
        break_lnum=line_z,
        filename=RE_THIS_FILE_CANONICAL,
    ))


# On Windows, it seems like this file is handled as cp1252-encoded instead
# of utf8 (even though the "# -*- coding: utf-8 -*-" line exists) and the
# core pdb code does not support that. Or something to that effect, I don't
# actually know.
# UnicodeDecodeError: 'charmap' codec can't decode byte 0x81 in position
# 6998: character maps to <undefined>.
# So we XFail this test on Windows.
@pytest.mark.xfail(
    sys.platform == "win32",
    raises=UnicodeDecodeError,
    strict=True,
    reason=(
        "Windows encoding issue. See comments and"
        " https://github.com/pdbpp/pdbpp/issues/341"
    ),
)
@pytest.mark.skipif(not hasattr(pdbpp.pdb.Pdb, "error"),
                    reason="no error method")
def test_continue_arg_with_error():
    def fn():
        set_trace()
        x = 1
        y = 2
        z = 3
        return x+y+z

    _, lineno = inspect.getsourcelines(fn)
    line_z = lineno + 4

    check(fn, r"""
[NUM] > .*fn()
-> x = 1
   5 frames hidden .*
# c.foo
\*\*\* The specified object '.foo' is not a function or was not found along sys.path.
# c {break_lnum}
Breakpoint NUM at {filename}:{break_lnum}
Deleted breakpoint NUM
[NUM] > .*fn()
-> z = 3
   5 frames hidden .*
# c
    """.format(
        break_lnum=line_z,
        filename=RE_THIS_FILE_CANONICAL,
    ))


@pytest.mark.skipif(sys.version_info < (3, 7), reason="header kwarg is 3.7+")
def test_set_trace_header():
    """Handler header kwarg added with Python 3.7 in pdb.set_trace."""
    def fn():
        set_trace_via_module(header="my_header")

    check(fn, """
my_header
--Return--
[NUM] > .*fn()
-> set_trace.*
   5 frames hidden .*
# c
""")


def test_stdout_encoding_None():
    instance = PdbTest()
    instance.stdout = BytesIO()
    instance.stdout.encoding = None

    instance.ensure_file_can_write_unicode(instance.stdout)

    try:
        import cStringIO
    except ImportError:
        pass
    else:
        instance.stdout = cStringIO.StringIO()
        instance.ensure_file_can_write_unicode(instance.stdout)


def test_frame_cmd_changes_locals():
    def a():
        x = 42  # noqa: F841
        b()

    def b():
        fn()

    def fn():
        set_trace()
        return

    check(a, """
[NUM] > .*fn()
-> return
   5 frames hidden .*
# f {frame_num_a}
[NUM] > .*a()
-> b()
# p list(sorted(locals().keys()))
['b', 'x']
# c
""".format(frame_num_a=count_frames() + 2 - 5))


@pytest.mark.skipif(not hasattr(pdbpp.pdb.Pdb, "_cmdloop"),
                    reason="_cmdloop is not available")
def test_sigint_in_interaction_with_new_cmdloop():
    def fn():
        def inner():
            raise KeyboardInterrupt()
        set_trace()

    check(fn, """
--Return--
[NUM] > .*fn()
-> set_trace()
   5 frames hidden .*
# debug inner()
ENTERING RECURSIVE DEBUGGER
[NUM] > .*
(#) c
--KeyboardInterrupt--
# c
""")


@pytest.mark.skipif(hasattr(pdbpp.pdb.Pdb, "_cmdloop"),
                    reason="_cmdloop is available")
def test_sigint_in_interaction_without_new_cmdloop():
    def fn():
        def inner():
            raise KeyboardInterrupt()
        set_trace()

    with pytest.raises(KeyboardInterrupt):
        check(fn, """
--Return--
[NUM] > .*fn()
-> set_trace()
   5 frames hidden .*
# debug inner()
ENTERING RECURSIVE DEBUGGER
[NUM] > .*
(#) c
""")

    # Reset pdb, which did not clean up correctly.
    # Needed for PyPy (Python 2.7.13[pypy-7.1.0-final]) with coverage and
    # restoring trace function.
    pdbpp.local.GLOBAL_PDB.reset()


def test_debug_rebind_globals(monkeypatch):
    class PdbWithCustomDebug(pdbpp.pdb.Pdb):
        def do_debug(self, arg):
            if "PdbTest" not in globals():
                # Do not use assert here, since it might fail with "NameError:
                # name '@pytest_ar' is not defined" via pytest's assertion
                # rewriting then.
                import pytest
                pytest.fail("PdbTest is not in globals.")
            print("called_do_debug", Pdb, self)  # noqa: F821

    monkeypatch.setattr(pdbpp.pdb, "Pdb", PdbWithCustomDebug)

    class CustomPdbTest(PdbTest, PdbWithCustomDebug):
        pass

    def fn():
        def inner():
            pass

        set_trace(Pdb=CustomPdbTest)

    check(fn, """
--Return--
[NUM] > .*fn()
-> set_trace(.*)
   5 frames hidden .*
# debug inner()
called_do_debug.*
# c
""")


@pytest.mark.skipif(not hasattr(pdbpp.pdb.Pdb, "_previous_sigint_handler"),
                    reason="_previous_sigint_handler is not available")
def test_interaction_restores_previous_sigint_handler():
    """Test is based on cpython's test_pdb_issue_20766."""
    def fn():
        i = 1
        while i <= 2:
            sess = PdbTest(nosigint=False)
            sess.set_trace(sys._getframe())
            print('pdb %d: %s' % (i, sess._previous_sigint_handler))
            i += 1

    check(fn, """
[NUM] > .*fn()
-> print('pdb %d: %s' % (i, sess._previous_sigint_handler))
   5 frames hidden .*
# c
pdb 1: <built-in function default_int_handler>
[NUM] > .*fn()
-> .*
   5 frames hidden .*
# c
pdb 2: <built-in function default_int_handler>
""")


def test_recursive_set_trace():
    def fn():
        global inner
        global count
        count = 0

        def inner():
            global count
            count += 1

            if count == 1:
                set_trace()
            else:
                set_trace(cleanup=False)

        inner()

    check(fn, """
--Return--
[NUM] > .*inner()
-> set_trace()
   5 frames hidden .*
# inner()
# c
""")


def test_steps_over_set_trace():
    def fn():
        set_trace()
        print(1)

        set_trace(cleanup=False)
        print(2)

    check(fn, """
[NUM] > .*fn()
-> print(1)
   5 frames hidden .*
# n
1
[NUM] > .*fn()
-> set_trace(cleanup=False)
   5 frames hidden .*
# n
[NUM] > .*fn()
-> print(2)
   5 frames hidden .*
# c
2
""")


def test_break_after_set_trace():
    def fn():
        set_trace()
        print(1)
        print(2)

    _, lineno = inspect.getsourcelines(fn)

    check(fn, """
[NUM] > .*fn()
-> print(1)
   5 frames hidden .*
# break {lineno}
Breakpoint . at .*:{lineno}
# c
1
[NUM] > .*fn()
-> print(2)
   5 frames hidden .*
# import pdb; pdbpp.local.GLOBAL_PDB.clear_all_breaks()
# c
2
""".format(lineno=lineno + 3))


def test_break_with_inner_set_trace():
    def fn():
        def inner():
            set_trace(cleanup=False)

        set_trace()
        inner()
        print(1)

    _, lineno = inspect.getsourcelines(fn)

    check(fn, """
[NUM] > .*fn()
-> inner()
   5 frames hidden .*
# break {lineno}
Breakpoint . at .*:{lineno}
# c
--Return--
[NUM] > .*inner()->None
-> set_trace(cleanup=False)
   5 frames hidden .*
# import pdb; pdbpp.local.GLOBAL_PDB.clear_all_breaks()
# c
1
""".format(lineno=lineno + 8))


@pytest.mark.skipif(
    sys.version_info < (3,), reason="no support for exit from interaction with pdbrc"
)
def test_pdbrc_continue(tmpdirhome):
    """Test that interaction is skipped with continue in pdbrc."""
    assert os.getcwd() == str(tmpdirhome)
    with open(".pdbrc", "w") as f:
        f.writelines([
            "p 'from_pdbrc'\n",
            "continue\n",
        ])

    def fn():
        set_trace(readrc=True)
        print("after_set_trace")

    check(fn, """
'from_pdbrc'
after_set_trace
""")


def test_python_m_pdb_usage():
    import subprocess

    p = subprocess.Popen(
        [sys.executable, "-m", "pdb"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    stdout, stderr = p.communicate()
    out = stdout.decode("utf8")
    err = stderr.decode("utf8")
    assert err == ""
    assert "usage: pdb.py" in out


@pytest.mark.parametrize('PDBPP_HIJACK_PDB', (1, 0))
def test_python_m_pdb_uses_pdbpp_and_env(PDBPP_HIJACK_PDB, monkeypatch, tmpdir):
    import subprocess
    import textwrap

    monkeypatch.setenv("PDBPP_HIJACK_PDB", str(PDBPP_HIJACK_PDB))

    f = tmpdir.ensure("test.py")
    f.write(textwrap.dedent("""
        import inspect
        import os
        import pdb

        fname = os.path.basename(inspect.getfile(pdb.Pdb))
        if {PDBPP_HIJACK_PDB}:
            assert fname == 'pdbpp.py', (fname, pdb, pdb.Pdb)
        else:
            assert fname in ('pdb.py', 'pdb.pyc'), (fname, pdb, pdb.Pdb)
        pdb.set_trace()
    """.format(PDBPP_HIJACK_PDB=PDBPP_HIJACK_PDB)))

    p = subprocess.Popen(
        [sys.executable, "-m", "pdb", str(f)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.PIPE
    )
    stdout, stderr = p.communicate(b"c\n")
    out = stdout.decode("utf8")
    err = stderr.decode("utf8")
    print(out)
    print(err, file=sys.stderr)
    assert err == ""
    if PDBPP_HIJACK_PDB:
        assert "(Pdb)" not in out
        assert "(Pdb++)" in out
        if sys.platform == 'win32' and sys.version_info < (3,):  # XXX ???
            assert out.endswith("\n(Pdb++) " + os.linesep)
        else:
            assert out.endswith("\n(Pdb++) \n")
    else:
        assert "(Pdb)" in out
        assert "(Pdb++)" not in out
        assert out.endswith("\n(Pdb) " + os.linesep)


def get_completions(text):
    """Get completions from the installed completer."""
    readline_ = pdbpp.local.GLOBAL_PDB.fancycompleter.config.readline
    complete = readline_.get_completer()
    comps = []
    assert complete.__self__ is pdbpp.local.GLOBAL_PDB
    while True:
        val = complete(text, len(comps))
        if val is None:
            break
        comps += [val]
    return comps


def test_set_trace_in_completion(monkeypatch_readline):
    def fn():
        class CompleteMe(object):
            attr_called = 0

            @property
            def set_trace_in_attrib(self):
                self.attr_called += 1
                set_trace(cleanup=False)
                print("inner_set_trace_was_ignored")

        obj = CompleteMe()

        def check_completions():
            monkeypatch_readline("obj.", 0, 4)
            comps = get_completions("obj.")
            assert obj.attr_called == 1, "attr was called"

            # Colorization only works with pyrepl, via pyrepl.readline._setup.
            assert any("set_trace_in_attrib" in comp for comp in comps), comps
            return True

        set_trace()

    check(fn, """
--Return--
[NUM] > .*fn()
.*
   5 frames hidden .*
# check_completions()
inner_set_trace_was_ignored
True
# c
""")


def test_completes_from_pdb(monkeypatch_readline):
    """Test that pdb's original completion is used."""
    def fn():
        where = 1  # noqa: F841
        set_trace()

        def check_completions():
            # Patch readline to return expected results for "wher".
            monkeypatch_readline("wher", 0, 4)
            assert get_completions("wher") == ["where"]

            if sys.version_info > (3, ):
                # Patch readline to return expected results for "disable ".
                monkeypatch_readline("disable", 8, 8)

                # NOTE: number depends on bpb.Breakpoint class state, just ensure that
                #       is a number.
                completion = pdbpp.local.GLOBAL_PDB.complete("", 0)
                assert int(completion) > 0

                # Patch readline to return expected results for "p ".
                monkeypatch_readline("p ", 2, 2)
                comps = get_completions("")
                assert "where" in comps

                # Dunder members get completed only on second invocation.
                assert "__name__" not in comps
                comps = get_completions("")
                assert "__name__" in comps

            # Patch readline to return expected results for "help ".
            monkeypatch_readline("help ", 5, 5)
            comps = get_completions("")
            assert "help" in comps

            return True

        set_trace()

    _, lineno = inspect.getsourcelines(fn)

    check(fn, """
[NUM] > .*fn()
.*
   5 frames hidden .*
# break %d
Breakpoint NUM at .*
# c
--Return--
[NUM] > .*fn()
.*
   5 frames hidden .*
# check_completions()
True
# c
""" % lineno)


def test_completion_uses_tab_from_fancycompleter(monkeypatch_readline):
    """Test that pdb's original completion is used."""
    def fn():
        def check_completions():
            # Patch readline to return expected results for "C.f()".
            monkeypatch_readline("C.f()", 5, 5)
            assert get_completions("") == ["\t"]
            return True

        set_trace()

    _, lineno = inspect.getsourcelines(fn)

    check(fn, """
--Return--
[NUM] > .*fn()->None
.*
   5 frames hidden .*
# check_completions()
True
# c
""")


def test_complete_removes_duplicates_with_coloring(
    monkeypatch_readline, readline_param
):
    def fn():
        helpvar = 42  # noqa: F841

        class obj:
            foo = 1
            foobar = 2

        def check_completions():
            # Patch readline to return expected results for "help".
            monkeypatch_readline("help", 0, 4)

            if readline_param == "pyrepl":
                assert pdbpp.local.GLOBAL_PDB.fancycompleter.config.use_colors is True
                assert get_completions("help") == [
                    "\x1b[000;00m\x1b[00mhelp\x1b[00m",
                    "\x1b[001;00m\x1b[33;01mhelpvar\x1b[00m",
                    " ",
                ]
            else:
                assert pdbpp.local.GLOBAL_PDB.fancycompleter.config.use_colors is False
                assert get_completions("help") == ["help", "helpvar"]

            # Patch readline to return expected results for "p helpvar.".
            monkeypatch_readline("p helpvar.", 2, 10)
            if readline_param == "pyrepl":
                assert pdbpp.local.GLOBAL_PDB.fancycompleter.config.use_colors is True
                comps = get_completions("helpvar.")
                assert type(helpvar.denominator) == int
                assert any(
                    re.match(r"\x1b\[\d\d\d;00m\x1b\[33;01mdenominator\x1b\[00m", x)
                    for x in comps
                )
                assert " " in comps
            else:
                assert pdbpp.local.GLOBAL_PDB.fancycompleter.config.use_colors is False
                comps = get_completions("helpvar.")
                assert "denominator" in comps
                assert " " not in comps

            monkeypatch_readline("p obj.f", 2, 7)
            comps = get_completions("obj.f")
            assert comps == ['obj.foo']

            monkeypatch_readline("p obj.foo", 2, 9)
            comps = get_completions("obj.foo")
            if readline_param == "pyrepl":
                assert comps == [
                    '\x1b[000;00m\x1b[33;01mfoo\x1b[00m',
                    '\x1b[001;00m\x1b[33;01mfoobar\x1b[00m',
                    ' ']
            else:
                assert comps == ['foo', 'foobar', ' ']

            monkeypatch_readline("disp", 0, 4)
            comps = get_completions("disp")
            assert comps == ['display']

            return True

        set_trace()

    _, lineno = inspect.getsourcelines(fn)

    check(fn, """
--Return--
[NUM] > .*fn()->None
.*
   5 frames hidden .*
# check_completions()
True
# c
""")


class TestCompleteUnit:
    def test_fancy_prefix_with_same_in_pdb(self, patched_completions):
        assert patched_completions(
            "p foo.b",
            ["foo.bar"],
            ["foo.bar", "foo.barbaz"]
        ) == ["foo.bar"]
        assert patched_completions(
            "p foo.b",
            ["foo.bar"],
            ["foo.bar", "foo.barbaz", "foo.barbaz2"]
        ) == ["foo.bar"]

    def test_fancy_prefix_with_more_pdb(self, patched_completions):
        assert patched_completions(
            "p foo.b", ["foo.bar"], ["foo.bar", "foo.barbaz", "something else"]
        ) == ["foo.bar", "foo.barbaz", "something else"]

    def test_fancy_with_no_pdb(self, patched_completions,
                               fancycompleter_color_param):

        if fancycompleter_color_param == "color":
            fancy = [
                "\x1b[000;00m\x1b[33;01mfoo\x1b[00m",
                "\x1b[001;00m\x1b[33;01mfoobar\x1b[00m",
                " ",
            ]
        else:
            fancy = [
                "foo",
                "foobar",
                " ",
            ]
        assert patched_completions("foo", fancy, []) == fancy

    def test_fancy_with_prefixed_pdb(self, patched_completions):
        assert patched_completions("sys.version", [
            "version",
            "version_info",
            " ",
        ], [
            "sys.version",
            "sys.version_info",
        ]) == ["version", "version_info", " "]

    def test_fancy_with_prefixed_pdb_other_text(self, patched_completions):
        fancy = ["version", "version_info"]
        pdb = ["sys.version", "sys.version_info"]
        assert patched_completions("xxx", fancy, pdb) == fancy + pdb

    def test_fancy_tab_without_pdb(self, patched_completions):
        assert patched_completions("", ["\t"], []) == ["\t"]

    def test_fancy_tab_with_pdb(self, patched_completions):
        assert patched_completions("", ["\t"], ["help"]) == ["help"]


def test_complete_uses_attributes_only_from_orig_pdb(
    monkeypatch_readline, readline_param
):
    def fn():
        def check_completions():
            # Patch readline to return expected results for "p sys.version".
            monkeypatch_readline("p sys.version", 2, 13)

            if readline_param == "pyrepl":
                assert pdbpp.local.GLOBAL_PDB.fancycompleter.config.use_colors is True
                assert get_completions("sys.version") == [
                    "\x1b[000;00m\x1b[32;01mversion\x1b[00m",
                    "\x1b[001;00m\x1b[00mversion_info\x1b[00m",
                    " ",
                ]
            else:
                assert pdbpp.local.GLOBAL_PDB.fancycompleter.config.use_colors is False
                assert get_completions("sys.version") == [
                    "version",
                    "version_info",
                    " ",
                ]
            return True

        set_trace()

    _, lineno = inspect.getsourcelines(fn)

    check(fn, """
--Return--
[NUM] > .*fn()->None
.*
   5 frames hidden .*
# import sys
# check_completions()
True
# c
""")


@pytest.mark.skipif(sys.version_info < (3, ), reason="py2: no completion for break")
def test_completion_removes_tab_from_fancycompleter(monkeypatch_readline):
    def fn():
        def check_completions():
            # Patch readline to return expected results for "b ".
            monkeypatch_readline("b ", 2, 2)
            comps = get_completions("")
            assert "\t" not in comps
            assert "inspect" in comps
            return True

        set_trace()

    _, lineno = inspect.getsourcelines(fn)

    check(fn, """
--Return--
[NUM] > .*fn()
.*
   5 frames hidden .*
# check_completions()
True
# c
""")


def test_integration(testdir, readline_param):
    """Integration test."""
    import sys

    tmpdir = testdir.tmpdir

    f = tmpdir.ensure("test_file.py")
    f.write("print('before'); __import__('pdb').set_trace(); print('after')")

    if readline_param != "pyrepl":
        # Create empty pyrepl module to ignore any installed pyrepl.
        mocked_pyrepl = tmpdir.ensure("pyrepl.py")
        mocked_pyrepl.write("")

    child = testdir.spawn(sys.executable + " test_file.py", expect_timeout=1)
    child.expect_exact("\n(Pdb++) ")

    if readline_param != "pyrepl":
        # Remove it after startup to not interfere with completions.
        mocked_pyrepl.remove()

    if readline_param == "pyrepl":
        child.expect_exact("\x1b[?12l\x1b[?25h")
        pdbpp_prompt = "\n(Pdb++) \x1b[?12l\x1b[?25h"
    else:
        pdbpp_prompt = "\n(Pdb++) "

    # Completes help as unique (coming from pdb and fancycompleter).
    child.send(b"hel\t")
    if readline_param == "pyrepl":
        child.expect_exact(b"\x1b[1@h\x1b[1@e\x1b[1@l\x1b[1@p")
    else:
        child.expect_exact(b"help")
    child.sendline("")
    child.expect_exact("\r\nDocumented commands")
    child.expect_exact(pdbpp_prompt)

    # Completes breakpoints via pdb, should not contain "\t" from
    # fancycompleter.
    if sys.version_info >= (3, 3):
        child.send(b"b \t")
        if readline_param == "pyrepl":
            child.expect_exact(b'\x1b[1@b\x1b[1@ \x1b[?25ltest_file.py:'
                               b'\x1b[?12l\x1b[?25h')
        else:
            child.expect_exact(b'b test_file.py:')

        child.sendline("")
        if readline_param == "pyrepl":
            child.expect_exact(
                b"\x1b[23D\r\n\r\x1b[?1l\x1b>*** Bad lineno: \r\n"
                b"\x1b[?1h\x1b=\x1b[?25l\x1b[1A\r\n(Pdb++) \x1b[?12l\x1b[?25h"
            )
        else:
            child.expect_exact(b"\r\n*** Bad lineno: \r\n(Pdb++) ")

    child.sendline("c")
    rest = child.read()
    if readline_param == "pyrepl":
        assert rest == b'\x1b[1@c\x1b[9D\r\n\r\x1b[?1l\x1b>'
    else:
        assert rest == b'c\r\n'


def test_complete_with_bang(monkeypatch_readline):
    """Test that completion works after "!".

    This requires parseline to return "" for the command (bpo-35270).
    """
    def fn():
        a_var = 1  # noqa: F841

        def check_completions():
            # Patch readline to return expected results for "!a_va".
            monkeypatch_readline("!a_va", 0, 5)
            assert pdbpp.local.GLOBAL_PDB.complete("a_va", 0) == "a_var"

            # Patch readline to return expected results for "list(a_va".
            monkeypatch_readline("list(a_va", 5, 9)
            assert pdbpp.local.GLOBAL_PDB.complete("a_va", 0) == "a_var"
            return True

        set_trace()

    check(fn, """
--Return--
[NUM] > .*fn()
.*
   5 frames hidden .*
# check_completions()
True
# c
""")


def test_completer_after_debug(monkeypatch_readline):
    def fn():
        myvar = 1  # noqa: F841

        def inner():
            myinnervar = 1  # noqa: F841

            def check_completions_inner():
                # Patch readline to return expected results for "myin".
                monkeypatch_readline("myin", 0, 4)
                assert "myinnervar" in get_completions("myin")
                return True

            print("inner_end")

        def check_completions():
            # Patch readline to return expected results for "myva".
            monkeypatch_readline("myva", 0, 4)
            assert "myvar" in get_completions("myva")
            return True

        set_trace()

        print("ok_end")
    check(fn, """
[NUM] > .*fn()
.*
   5 frames hidden .*
# pdbpp.local.GLOBAL_PDB.curframe.f_code.co_name
'fn'
# debug inner()
ENTERING RECURSIVE DEBUGGER
[1] > <string>(1)<module>()
(#) pdbpp.local.GLOBAL_PDB.curframe.f_code.co_name
'<module>'
(#) s
--Call--
[NUM] > .*inner()
-> def inner():
(#) pdbpp.local.GLOBAL_PDB.curframe.f_code.co_name
'inner'
(#) r
inner_end
--Return--
[NUM] > .*inner()->None
-> print("inner_end")
(#) check_completions_inner()
True
(#) q
LEAVING RECURSIVE DEBUGGER
# check_completions()
True
# c
ok_end
""")


def test_ensure_file_can_write_unicode():
    import io
    from pdb import DefaultConfig, Pdb

    out = io.BytesIO(b"")
    stdout = io.TextIOWrapper(out, encoding="latin1")

    p = Pdb(Config=DefaultConfig, stdout=stdout)

    assert p.stdout.stream is out

    p.stdout.write(u"test äöüß")
    out.seek(0)
    assert out.read().decode("utf-8") == u"test äöüß"


@pytest.mark.skipif(sys.version_info >= (3, 0),
                    reason="test is python2 specific")
def test_py2_ensure_file_can_write_unicode():
    import StringIO
    from pdb import DefaultConfig, Pdb

    stdout = StringIO.StringIO()
    stdout.encoding = 'ascii'

    p = Pdb(Config=DefaultConfig, stdout=stdout)

    assert p.stdout.stream is stdout

    p.stdout.write(u"test äöüß")
    stdout.seek(0)
    assert stdout.read().decode('utf-8') == u"test äöüß"


def test_signal_in_nonmain_thread_with_interaction():
    def fn():
        import threading

        evt = threading.Event()

        def start_thread():
            evt.wait()
            set_trace(nosigint=False)

        t = threading.Thread(target=start_thread)
        t.start()
        set_trace(nosigint=False)
        evt.set()
        t.join()

    check(fn, """
[NUM] > .*fn()
-> evt.set()
   5 frames hidden .*
# c
--Return--
[NUM] > .*start_thread()->None
-> set_trace(nosigint=False)
# c
""")


def test_signal_in_nonmain_thread_with_continue():
    """Test for cpython issue 13120 (test_issue13120).

    Without the try/execept for ValueError in its do_continue it would
    display the exception, but work otherwise.
    """
    def fn():
        import threading

        def start_thread():
            a = 42  # noqa F841
            set_trace(nosigint=False)

        t = threading.Thread(target=start_thread)
        t.start()
        # set_trace(nosigint=False)
        t.join()

    check(fn, """
--Return--
[NUM] > .*start_thread()->None
-> set_trace(nosigint=False)
# p a
42
# c
""")


def test_next_at_end_of_stack_after_unhide():
    """Test that compute_stack returns correct length with show_hidden_frames."""
    class MyConfig(ConfigTest):
        def before_interaction_hook(self, pdb):
            pdb.stdout.write('before_interaction_hook\n')
            pdb.do_hf_unhide(arg=None)

    def fn():
        set_trace(Config=MyConfig)
        return 1

    check(fn, """
[NUM] > .*fn()
-> return 1
   5 frames hidden .*
before_interaction_hook
# n
--Return--
[NUM] > .*fn()->1
-> return 1
   5 frames hidden .*
before_interaction_hook
# c
""")


def test_rawinput_with_debug():
    """Test backport of fix for bpo-31078."""
    def fn():
        set_trace()

    check(fn, """
--Return--
[NUM] > .*fn()
-> set_trace()
   5 frames hidden .*
# debug 1
ENTERING RECURSIVE DEBUGGER
[NUM] > <string>(1)<module>()->None
(#) import pdb; print(pdbpp.local.GLOBAL_PDB.use_rawinput)
1
(#) p sys._getframe().f_back.f_locals['self'].use_rawinput
1
(#) c
LEAVING RECURSIVE DEBUGGER
# c
""")


def test_error_with_traceback():
    def fn():
        def error():
            raise ValueError("error")

        set_trace()

    check(fn, """
--Return--
[NUM] > .*fn()
-> set_trace()
   5 frames hidden .*
# error()
\\*\\*\\* ValueError: error
Traceback (most recent call last):
  File .*, in error
    raise ValueError("error")
# c
""")


def test_chained_syntaxerror_with_traceback():
    def fn():
        def compile_error():
            compile("invalid(", "<stdin>", "single")

        def error():
            try:
                compile_error()
            except Exception:
                raise AttributeError

        set_trace()

    if sys.version_info > (3,):
        check(fn, """
--Return--
[NUM] > .*fn()
-> set_trace()
   5 frames hidden .*
# error()
\\*\\*\\* AttributeError.*
Traceback (most recent call last):
  File .*, in error
    compile_error()
  File .*, in compile_error
    compile.*
  File "<stdin>", line 1
    invalid(
    .*^
SyntaxError: .*

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File .*, in error
    raise AttributeError
# c
""")
    else:
        check(fn, """
--Return--
[NUM] > .*fn()
-> set_trace()
   5 frames hidden .*
# error()
\\*\\*\\* AttributeError.*
Traceback (most recent call last):
  File .*, in error
    raise AttributeError
# c
""")


def test_error_with_traceback_disabled():
    class ConfigWithoutTraceback(ConfigTest):
        show_traceback_on_error = False

    def fn():
        def error():
            raise ValueError("error")

        set_trace(Config=ConfigWithoutTraceback)

    check(fn, """
--Return--
[NUM] > .*fn()
-> set_trace(Config=ConfigWithoutTraceback)
   5 frames hidden .*
# error()
\\*\\*\\* ValueError: error
# c
""")


def test_error_with_traceback_limit():
    class ConfigWithLimit(ConfigTest):
        show_traceback_on_error_limit = 2

    def fn():
        def f(i):
            i -= 1
            if i <= 0:
                raise ValueError("the_end")
            f(i)

        def error():
            f(10)

        set_trace(Config=ConfigWithLimit)

    check(fn, """
--Return--
[NUM] > .*fn()
-> set_trace(Config=ConfigWithLimit)
   5 frames hidden .*
# error()
\\*\\*\\* ValueError: the_end
Traceback (most recent call last):
  File .*, in error
    f(10)
  File .*, in f
    f(i)
# c
""")


def test_next_with_exception_in_call():
    """Ensure that "next" works correctly with exception (in try/except).

    Previously it would display the frame where the exception occurred, and
    then "next" would continue, instead of stopping at the next statement.
    """
    def fn():
        def keyerror():
            raise KeyError

        set_trace()
        try:
            keyerror()
        except KeyError:
            print("got_keyerror")

    check(fn, """
[NUM] > .*fn()
-> try:
   5 frames hidden .*
# n
[NUM] > .*fn()
-> keyerror()
   5 frames hidden .*
# n
KeyError
[NUM] > .*fn()
-> keyerror()
   5 frames hidden .*
# n
[NUM] > .*fn()
-> except KeyError:
   5 frames hidden .*
# c
got_keyerror
""")


def test_locals():
    def fn():
        def f():
            set_trace()
            print("foo=%s" % foo)  # noqa: F821
            foo = 2  # noqa: F841

        f()

    check(fn, """
[NUM] > .*f()
-> print("foo=%s" % foo)
   5 frames hidden .*
# foo = 42
# foo
42
# pp foo
42
# p foo
42
# c
foo=42
""")


def test_locals_with_list_comprehension():
    def fn():
        mylocal = 1  # noqa: F841
        set_trace()
        print(mylocal)

    check(fn, """
[NUM] > .*fn()
-> print(mylocal)
   5 frames hidden .*
# mylocal
1
# [x for x in str(mylocal)]
['1']
# [mylocal for x in range(1)]
[1]
# mylocal = 42
# [x for x in str(mylocal)]
['4', '2']
# [mylocal for x in range(1)]
[42]
# c
42
""")


def test_get_editor_cmd(monkeypatch):
    _pdb = PdbTest()

    _pdb.config.editor = None
    monkeypatch.setenv("EDITOR", "nvim")
    assert _pdb._get_editor_cmd("fname", 42) == "nvim +42 fname"

    monkeypatch.setenv("EDITOR", "")
    with pytest.raises(RuntimeError, match=(
            r"Could not detect editor. Configure it or set \$EDITOR."
    )):
        _pdb._get_editor_cmd("fname", 42)

    monkeypatch.delenv("EDITOR")

    try:
        which = "shutil.which"
        monkeypatch.setattr(which, lambda x: None)
    except AttributeError:
        which = "distutils.spawn.find_executable"
        monkeypatch.setattr(which, lambda x: None)
    with pytest.raises(RuntimeError, match=(
            r"Could not detect editor. Configure it or set \$EDITOR."
    )):
        _pdb._get_editor_cmd("fname", 42)

    monkeypatch.setattr(which, lambda x: "vim")
    assert _pdb._get_editor_cmd("fname", 42) == "vim +42 fname"
    monkeypatch.setattr(which, lambda x: "vi")
    assert _pdb._get_editor_cmd("fname", 42) == "vi +42 fname"

    _format = _pdb._format_editcmd
    assert _format("subl {filename}:{lineno}", "with space", 12) == (
        "subl 'with space':12")
    assert _format("edit", "with space", 12) == (
        "edit +12 'with space'")
    assert _format("edit +%%%d %%%s%% %d", "with space", 12) == (
        "edit +%12 %'with space'% 12")


def test_edit_error(monkeypatch):
    class MyConfig(ConfigTest):
        editor = None

    monkeypatch.setenv("EDITOR", "")

    def fn():
        set_trace(Config=MyConfig)

    check(fn, r"""
--Return--
[NUM] > .*fn()
-> set_trace(Config=MyConfig)
   5 frames hidden .*
# edit
\*\*\* Could not detect editor. Configure it or set \$EDITOR.
# c
""")


def test_global_pdb_per_thread_with_input_lock():
    def fn():
        import threading

        evt1 = threading.Event()
        evt2 = threading.Event()

        def __t1__(evt1, evt2):
            set_trace(cleanup=False)

        def __t2__(evt2):
            evt2.set()
            set_trace(cleanup=False)

        t1 = threading.Thread(name="__t1__", target=__t1__, args=(evt1, evt2))
        t1.start()

        assert evt1.wait(1.0) is True
        t2 = threading.Thread(name="__t2__", target=__t2__, args=(evt2,))
        t2.start()

        t1.join()
        t2.join()

    check(fn, r"""
--Return--
[NUM] > .*__t1__()
-> set_trace(cleanup=False)
# evt1.set()
# import threading; threading.current_thread().name
'__t1__'
# assert evt2.wait(1.0) is True; import time; time.sleep(0.1)
--Return--
[NUM] > .*__t2__()->None
-> set_trace(cleanup=False)
# import threading; threading.current_thread().name
'__t2__'
# c
# import threading; threading.current_thread().name
'__t1__'
# c
""")


def test_usage_error_with_commands():
    def fn():
        set_trace()

    check(fn, r"""
--Return--
[NUM] > .*fn()->None
-> set_trace()
   5 frames hidden .*
# commands invalid
.*Usage.*: commands [bnum]
        ...
        end
# c
""")


@pytest.mark.skipif(sys.version_info < (3,),
                    reason="py2 has no support for kwonly")
def test_rebind_globals_kwonly():
    exec("def func(*args, header=None): pass", globals())
    func = globals()["func"]

    sig = str(inspect.signature(func))
    assert sig == "(*args, header=None)"
    new = pdbpp.rebind_globals(func, globals())
    assert str(inspect.signature(new)) == sig


@pytest.mark.skipif(sys.version_info < (3,),
                    reason="py2 has no support for annotations")
def test_rebind_globals_annotations():
    exec("def func(ann: str = None): pass", globals())
    func = globals()["func"]

    sig = str(inspect.signature(func))
    if sys.version_info < (3, 5):
        assert sig == "(ann:str=None)"
    else:
        assert sig in (
             "(ann: str = None)",
             "(ann:str=None)",
        )
    new = pdbpp.rebind_globals(func, globals())
    assert str(inspect.signature(new)) == sig


def test_rebind_globals_with_partial():
    import functools

    global test_global
    test_global = 0

    def func(a, b):
        global test_global
        return a + b + test_global

    pfunc = functools.partial(func)
    assert pfunc(0, 0) == 0

    newglobals = globals().copy()
    newglobals['test_global'] = 1
    new = pdbpp.rebind_globals(pfunc, newglobals)
    assert new(1, 40) == 42


def test_debug_with_set_trace():
    def fn():
        def inner():
            def inner_inner():
                pass

            set_trace(cleanup=False)

        set_trace()
    check(fn, """
--Return--
[NUM] > .*fn()
.*
   5 frames hidden .*
# debug inner()
ENTERING RECURSIVE DEBUGGER
[NUM] > <string>(1)<module>()->None
(#) r
--Return--
[NUM] > .*inner()->None
-> set_trace(cleanup=False)
   5 frames hidden .*
(#) pdbpp.local.GLOBAL_PDB.curframe.f_code.co_name
'inner'
(#) debug inner_inner()
ENTERING RECURSIVE DEBUGGER
[NUM] > <string>(1)<module>()->None
((#)) c
LEAVING RECURSIVE DEBUGGER
(#) c
LEAVING RECURSIVE DEBUGGER
# c
""")


def test_set_trace_with_incomplete_pdb():
    def fn():
        existing_pdb = PdbTest()
        assert not hasattr(existing_pdb, "botframe")

        set_trace(cleanup=False)

        assert hasattr(existing_pdb, "botframe")
        assert pdbpp.local.GLOBAL_PDB is existing_pdb
    check(fn, """
[NUM] > .*fn()
.*
   5 frames hidden .*
# c
""")


def test_config_gets_start_filename():
    def fn():
        setup_lineno = set_trace.__code__.co_firstlineno + 8
        set_trace_lineno = sys._getframe().f_lineno + 8

        class MyConfig(ConfigTest):
            def setup(self, pdb):
                print("config_setup")
                assert pdb.start_filename.lower() == THIS_FILE_CANONICAL.lower()
                assert pdb.start_lineno == setup_lineno

        set_trace(Config=MyConfig)

        assert pdbpp.local.GLOBAL_PDB.start_lineno == set_trace_lineno

    check(fn, r"""
config_setup
[NUM] > .*fn()
-> assert pdbpp.local.GLOBAL_PDB.start_lineno == set_trace_lineno
   5 frames hidden .*
# c
""")


def test_do_bt():
    def fn():
        set_trace()

    expected_bt = []
    for i, entry in enumerate(traceback.extract_stack()[:-3]):
        expected_bt.append("  [%2d] .*" % i)
        expected_bt.append("  .*")

    check(fn, r"""
--Return--
[NUM] > .*fn()->None
-> set_trace()
   5 frames hidden .*
# bt
{expected}
  [NUM] .*(NUM)runpdb()
       func()
> [NUM] .*(NUM)fn()->None
       set_trace()
# c
""".format(expected="\n".join(expected_bt)))


def test_do_bt_highlight():
    def fn():
        set_trace(Config=ConfigWithHighlight)

    expected_bt = []
    for i, entry in enumerate(traceback.extract_stack()[:-3]):
        expected_bt.append("  [%2d] .*" % i)
        expected_bt.append("  .*")

    check(fn, r"""
--Return--
[NUM] > .*fn()->None
-> set_trace(Config=ConfigWithHighlight)
   5 frames hidden .*
# bt
{expected}
  [NUM] ^[[33;01m.*\.py^[[00m(^[[36;01mNUM^[[00m)runpdb()
       func()
> [NUM] ^[[33;01m.*\.py^[[00m(^[[36;01mNUM^[[00m)fn()->None
       set_trace(Config=ConfigWithHighlight)
# c
""".format(expected="\n".join(expected_bt)))


def test_do_bt_pygments():
    def fn():
        set_trace(Config=ConfigWithPygments)

    expected_bt = []
    for i, entry in enumerate(traceback.extract_stack()[:-3]):
        expected_bt.append("  [%2d] .*" % i)
        expected_bt.append("  .*")

    check(fn, r"""
--Return--
[NUM] > .*fn()->None
-> set_trace(Config^[[38;5;241m=^[[39mConfigWithPygments)
   5 frames hidden .*
# bt
{expected}
  [NUM] .*(NUM)runpdb()
       func()
> [NUM] .*\.py(NUM)fn()->None
       set_trace(Config^[[38;5;241m=^[[39mConfigWithPygments)
# c
""".format(expected="\n".join(expected_bt)))


def test_debug_with_pygments():
    def fn():
        set_trace(Config=ConfigWithPygments)

    check(fn, r"""
--Return--
[NUM] > .*fn()->None
-> set_trace(Config^[[38;5;241m=^[[39mConfigWithPygments)
   5 frames hidden .*
# debug 1
ENTERING RECURSIVE DEBUGGER
[1] > <string>(1)<module>()->None
(#) c
LEAVING RECURSIVE DEBUGGER
# c
""")


def test_debug_with_pygments_and_highlight():
    def fn():
        set_trace(Config=ConfigWithPygmentsAndHighlight)

    check(fn, r"""
--Return--
[NUM] > .*fn()->None
-> set_trace(Config^[[38;5;241m=^[[39mConfigWithPygmentsAndHighlight)
   5 frames hidden .*
# debug 1
ENTERING RECURSIVE DEBUGGER
[1] > ^[[33;01m<string>^[[00m(^[[36;01m1^[[00m)<module>()->None
(#) c
LEAVING RECURSIVE DEBUGGER
# c
""")


def test_set_trace_in_default_code():
    """set_trace while not tracing and should not (re)set the global pdb."""
    def fn():
        def f():
            before = pdbpp.local.GLOBAL_PDB
            set_trace(cleanup=False)
            assert before is pdbpp.local.GLOBAL_PDB
        set_trace()

    check(fn, r"""
--Return--
[NUM] > .*fn()->None
-> set_trace()
   5 frames hidden .*
# f()
# import pdbpp; pdbpp.local.GLOBAL_PDB.curframe is not None
True
# l {line_num}, 2
NUM \t    def fn():
NUM \t        def f():
NUM \t            before = pdbpp.local.GLOBAL_PDB
# c
    """.format(
        line_num=fn.__code__.co_firstlineno,
    ))


def test_error_with_pp():
    def fn():
        class BadRepr:
            def __repr__(self):
                raise Exception('repr_exc')

        obj = BadRepr()  # noqa: F841
        set_trace()

    check(fn, r"""
--Return--
[NUM] > .*fn()->None
-> set_trace()
   5 frames hidden .*
# p obj
\*\*\* Exception: repr_exc
# pp obj
\*\*\* Exception: repr_exc
# pp BadRepr.__repr__()
\*\*\* TypeError: .*__repr__.*
# c
""")


def test_count_with_pp():
    def fn():
        set_trace()

    check(fn, r"""
--Return--
[NUM] > .*fn()->None
-> set_trace()
   5 frames hidden .*
# pp [1, 2, 3]
[1, 2, 3]
# 2pp [1, 2, 3]
[1,
 2,
 3]
# 80pp [1, 2, 3]
[1, 2, 3]
# c
""")


def test_ArgWithCount():
    from pdb import ArgWithCount

    obj = ArgWithCount("", None)
    assert obj == ""
    assert repr(obj) == "<ArgWithCount cmd_count=None value=''>"
    assert isinstance(obj, str)

    obj = ArgWithCount("foo", 42)
    assert obj == "foo"
    assert repr(obj) == "<ArgWithCount cmd_count=42 value='foo'>"


def test_do_source():
    def fn():
        set_trace()

    check(fn, r"""
--Return--
[NUM] > .*fn()->None
-> set_trace()
   5 frames hidden .*
# source ConfigWithPygmentsAndHighlight
\d\d     class ConfigWithPygmentsAndHighlight(ConfigWithPygments, ConfigWithHigh$
\d\d         pass
# c
""")


def test_do_source_with_pygments():
    def fn():
        set_trace(Config=ConfigWithPygments)

    check(fn, r"""
--Return--
[NUM] > .*fn()->None
-> set_trace(Config^[[38;5;241m=^[[39mConfigWithPygments)
   5 frames hidden .*
# source ConfigWithPygmentsAndHighlight
\d\d     ^[[38;5;28;01mclass^[[39;00m ^[[38;5;21;01mConfigWithPygmentsAndHighlight^[[39;00m(ConfigWithPygments, ConfigWithHigh$
\d\d         ^[[38;5;28;01mpass^[[39;00m
# c
""")  # noqa: E501


def test_do_source_with_highlight():
    def fn():
        set_trace(Config=ConfigWithHighlight)

    check(fn, r"""
--Return--
[NUM] > .*fn()->None
-> set_trace(Config=ConfigWithHighlight)
   5 frames hidden .*
# source ConfigWithPygmentsAndHighlight
^[[36;01m\d\d^[[00m     class ConfigWithPygmentsAndHighlight(ConfigWithPygments, ConfigWithHigh$
^[[36;01m\d\d^[[00m         pass
# c
""")  # noqa: E501


def test_do_source_with_pygments_and_highlight():
    def fn():
        set_trace(Config=ConfigWithPygmentsAndHighlight)

    check(fn, r"""
--Return--
[NUM] > .*fn()->None
-> set_trace(Config^[[38;5;241m=^[[39mConfigWithPygmentsAndHighlight)
   5 frames hidden .*
# source ConfigWithPygmentsAndHighlight
^[[36;01m\d\d^[[00m     ^[[38;5;28;01mclass^[[39;00m ^[[38;5;21;01mConfigWithPygmentsAndHighlight^[[39;00m(ConfigWithPygments, ConfigWithHigh$
^[[36;01m\d\d^[[00m         ^[[38;5;28;01mpass^[[39;00m
# c
""")  # noqa: E501


def test_do_source_without_truncating():
    def fn():
        class Config(ConfigTest):
            truncate_long_lines = False

        set_trace(Config=Config)

    check(fn, r"""
--Return--
[NUM] > .*fn()->None
-> set_trace(Config=Config)
   5 frames hidden .*
# source ConfigWithPygmentsAndHighlight
\d\d     class ConfigWithPygmentsAndHighlight(ConfigWithPygments, ConfigWithHighlight):$
\d\d         pass
# c
""")


def test_handles_set_trace_in_config(tmpdir):
    """Should not cause a RecursionError."""
    def fn():
        class Config(ConfigTest):
            def __init__(self, *args, **kwargs):
                print("Config.__init__")
                # Becomes a no-op.
                set_trace(Config=Config)
                print("after_set_trace")

        set_trace(Config=Config)

    check(fn, r"""
Config.__init__
pdb\+\+: using pdb.Pdb for recursive set_trace.
> .*__init__()
-> print("after_set_trace")
(Pdb) c
after_set_trace
--Return--
[NUM] > .*fn()->None
-> set_trace(Config=Config)
   5 frames hidden .*
# c
""")


def test_only_question_mark(monkeypatch):
    def fn():
        set_trace()
        a = 1
        return a

    monkeypatch.setattr(PdbTest, "do_help", lambda self, arg: print("do_help"))

    check(fn, """
[NUM] > .*fn()
-> a = 1
   5 frames hidden .*
# ?
do_help
# c
""")


@pytest.mark.parametrize('s,maxlength,expected', [
    ('foo', 3, 'foo'),
    ('foo', 1, 'f'),

    # Keeps trailing escape sequences (for reset at least).
    ("\x1b[39m1\x1b[39m23", 1, "\x1b[39m1\x1b[39m"),
    ("\x1b[39m1\x1b[39m23", 2, "\x1b[39m1\x1b[39m2"),
    ("\x1b[39m1\x1b[39m23", 3, "\x1b[39m1\x1b[39m23"),
    ("\x1b[39m1\x1b[39m23", 100, "\x1b[39m1\x1b[39m23"),

    ("\x1b[39m1\x1b[39m", 100, "\x1b[39m1\x1b[39m"),
])
def test_truncate_to_visible_length(s, maxlength, expected):
    assert pdbpp.Pdb._truncate_to_visible_length(s, maxlength) == expected


@pytest.mark.parametrize("pass_stdout", (True, False))
def test_stdout_reconfigured(pass_stdout, monkeypatch):
    """Check that self.stdout is re-configured with global pdb."""
    def fn():
        import sys
        if sys.version_info > (3,):
            from io import StringIO
        else:
            from StringIO import StringIO

        patched_stdout = StringIO()

        with monkeypatch.context() as mp:
            mp.setattr(sys, "stdout", patched_stdout)

            class _PdbTestKeepRawInput(PdbTest):
                def __init__(
                    self, completekey="tab", stdin=None, stdout=None, *args, **kwargs
                ):
                    if pass_stdout:
                        stdout = sys.stdout
                    else:
                        stdout = None
                    super(_PdbTestKeepRawInput, self).__init__(
                        completekey, stdin, stdout, *args, **kwargs
                    )
                    # Keep this, which gets set to 0 with stdin being passed in.
                    self.use_rawinput = True

            set_trace(Pdb=_PdbTestKeepRawInput)
            assert pdbpp.local.GLOBAL_PDB.stdout is patched_stdout

            print(patched_stdout.getvalue())
            patched_stdout.close()

        set_trace(Pdb=_PdbTestKeepRawInput, cleanup=False)
        assert pdbpp.local.GLOBAL_PDB.stdout is sys.stdout
        print('# c')  # Hack to reflect output in test.
        return

    check(fn, """
[NUM] > .*fn()
-> assert pdbpp.local.GLOBAL_PDB.stdout is sys.stdout
   5 frames hidden .*
# c
# c
""")


def test_position_of_obj_unwraps():
    import contextlib

    @contextlib.contextmanager
    def cm():
        raise NotImplementedError()

    pdb_ = PdbTest()
    pos = pdb_._get_position_of_obj(cm)

    if hasattr(inspect, "unwrap"):
        assert pos[0] == THIS_FILE_CANONICAL
        assert pos[2] == [
            "    @contextlib.contextmanager\n",
            "    def cm():\n",
            "        raise NotImplementedError()\n",
        ]
    else:
        contextlib_file = contextlib.__file__
        if sys.platform == 'win32':
            contextlib_file = contextlib_file.lower()
        assert pos[0] == contextlib_file.rstrip("c")


def test_set_trace_in_skipped_module(testdir):
    def fn():
        class SkippingPdbTest(PdbTest):
            def __init__(self, *args, **kwargs):
                kwargs["skip"] = ["testing.test_pdb"]
                super(SkippingPdbTest, self).__init__(*args, **kwargs)

                self.calls = []

            def is_skipped_module(self, module_name):
                self.calls.append(module_name)
                if len(self.calls) == 1:
                    print("is_skipped_module?", module_name)
                    ret = super(SkippingPdbTest, self).is_skipped_module(module_name)
                    assert module_name == "testing.test_pdb"
                    assert ret is True
                    return True
                return False

        set_trace(Pdb=SkippingPdbTest)  # 1
        set_trace(Pdb=SkippingPdbTest, cleanup=False)  # 2
        set_trace(Pdb=SkippingPdbTest, cleanup=False)  # 3

    check(fn, r"""
[NUM] > .*fn()
-> set_trace(Pdb=SkippingPdbTest, cleanup=False)  # 2
   5 frames hidden (try 'help hidden_frames')
# n
is_skipped_module\? testing.test_pdb
[NUM] > .*fn()
-> set_trace(Pdb=SkippingPdbTest, cleanup=False)  # 3
   5 frames hidden (try 'help hidden_frames')
# c
--Return--
[NUM] > .*fn()
-> set_trace(Pdb=SkippingPdbTest, cleanup=False)  # 3
   5 frames hidden (try 'help hidden_frames')
# c
""")

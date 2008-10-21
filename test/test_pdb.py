import inspect
import os.path
import sys
import pdb
import re
from cStringIO import StringIO
import py

class FakeStdin:
    def __init__(self, lines):
        self.lines = iter(lines)

    def readline(self):
        try:
            line = self.lines.next() + '\n'
            sys.stdout.write(line)
            return line
        except StopIteration:
            return ''

class ConfigTest(pdb.DefaultConfig):
    highlight = False
    prompt = '# ' # because + has a special meaning in the regexp
    editor = 'emacs'


class PdbTest(pdb.Pdb):
    use_rawinput = 1

    def __init__(self):
        pdb.Pdb.__init__(self, Config=ConfigTest)

    def _open_editor(self, editor, lineno, filename):
        print "RUN %s +%d '%s'" % (editor, lineno, filename)


def set_trace():
    PdbTest().set_trace(sys._getframe().f_back)

def xpm():
    pdb.xpm(PdbTest)

def runpdb(func, input):
    oldstdin = sys.stdin
    oldstdout = sys.stdout

    try:
        sys.stdin = FakeStdin(input)
        sys.stdout = stdout = StringIO()
        func()
    finally:
        sys.stdin = oldstdin
        sys.stdout = oldstdout

    return stdout.getvalue().splitlines()

def extract_commands(lines):
    return [line[2:] for line in lines if line.startswith('# ')]

shortcuts = {
    '(': '\\(',
    ')': '\\)',
    'NUM': ' *[0-9]*',
    'CLEAR': re.escape(pdb.CLEARSCREEN),
    }

def cook_regexp(s):
    for key, value in shortcuts.iteritems():
        s = s.replace(key, value)
    return s

def check(func, expected):
    expected = expected.strip().splitlines()
    commands = extract_commands(expected)
    expected = map(cook_regexp, expected)
    lines = runpdb(func, commands)
    assert len(expected) == len(lines)
    for pattern, string in zip(expected, lines):
        assert re.match(pattern, string)


def test_runpdb():
    def fn():
        set_trace()
        a = 1
        b = 2
        c = 3
        return a+b+c

    check(fn, """
> .*fn()
-> a = 1
# n
> .*fn()
-> b = 2
# n
> .*fn()
-> c = 3
# c
""")

def test_parseline():
    def fn():
        r = 42
        set_trace()
        return r

    check(fn, """
> .*fn()
-> return r
# r
42
# c
""")

def test_longlist():
    def fn():
        a = 1
        set_trace()
        return a

    check(fn, """
> .*fn()
-> return a
# ll
NUM         def fn():
NUM             a = 1
NUM             set_trace()
NUM  ->         return a
# c
""".replace('NUM', ' *[0-9]*'))

def test_watch():
    def fn():
        a = 1
        set_trace()
        b = 1
        a = 2
        a = 3
        return a

    check(fn, """
> .*fn()
-> b = 1
# watch a
# n
> .*fn()
-> a = 2
# n
> .*fn()
-> a = 3
a: 1 --> 2
# unwatch a
# n
> .*fn()
-> return a
# c
""")

def test_watch_undefined():
    def fn():
        set_trace()
        b = 42
        return b

    check(fn, """
> .*fn()
-> b = 42
# watch b
# n
> .*fn()
-> return b
b: <undefined> --> 42
# c
""")

def test_sticky():
    def fn():
        set_trace()
        a = 1
        b = 2
        c = 3
        return a

    check(fn, """
> .*fn()
-> a = 1
# sticky
CLEAR>.*

NUM         def fn():
NUM             set_trace()
NUM  ->         a = 1
NUM             b = 2
NUM             c = 3
NUM             return a
# n
> .*fn()
-> b = 2
CLEAR>.*

NUM         def fn():
NUM             set_trace()
NUM             a = 1
NUM  ->         b = 2
NUM             c = 3
NUM             return a
# sticky
# n
> .*fn()
-> c = 3
# c
""")

def test_sticky_range():
    import inspect
    def fn():
        set_trace()
        a = 1
        b = 2
        c = 3
        return a
    _, lineno = inspect.getsourcelines(fn)
    start = lineno + 1
    end = lineno + 3

    check(fn, """
> .*fn()
-> a = 1
# sticky %d %d
CLEAR>.*

 %d             set_trace()
NUM  ->         a = 1
NUM             b = 2
# c
""" % (start, end, start))


def test_exception_lineno():
    def bar():
        assert False
    def fn():
        try:
            a = 1
            bar()
            b = 2
        except AssertionError:
            xpm()

    check(fn, """
> .*bar()
-> assert False
# u
> .*fn()
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
""")

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
> .*gen()
-> assert False
# u
> .*fn()
-> for i in gen():
# c
""")

def test_py_code_source():
    src = py.code.Source("""
    def fn():
        x = 42
        set_trace()
        return x
    """)
    
    exec src.compile()
    check(fn, """
> .*fn()
# ll
NUM     
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
> .*fn()
-> return bar()
# source bar
NUM         def bar():
NUM             return 42
# c
""")

def test_bad_source():
    def fn():
        set_trace()
        return 42

    check(fn, r"""
> .*fn()
-> return 42
# source 42
\*\* Error: arg is not a module, class, method, function, traceback, frame, or code object \*\*
# c
""")

def test_edit():
    def fn():
        set_trace()
        return 42
    def bar():
        fn()
        return 100

    _, lineno = inspect.getsourcelines(fn)
    return42_lineno = lineno + 2
    call_fn_lineno = lineno + 4
    filename = os.path.abspath(__file__)
    if filename.endswith('.pyc'):
        filename = filename[:-1]
    
    check(fn, r"""
> .*fn()
-> return 42
# edit
RUN emacs \+%d '%s'
# c
""" % (return42_lineno, filename))

    check(bar, r"""
> .*fn()
-> return 42
# up
> .*bar()
-> fn()
# edit
RUN emacs \+%d '%s'
# c
""" % (call_fn_lineno, filename))


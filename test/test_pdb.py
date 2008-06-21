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


class PdbTest(pdb.Pdb):
    use_rawinput = 1

    def __init__(self):
        pdb.Pdb.__init__(self, ConfigTest)

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
a = 2
# unwatch a
# n
> .*fn()
-> return a
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
-> xpm()
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

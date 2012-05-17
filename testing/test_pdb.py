# -*- coding: utf-8 -*-
import inspect
import os.path
import sys
import re
from cStringIO import StringIO
import py

# make sure that we are really importing our pdb
sys.modules.pop('pdb', None)
import pdb

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
    stdin_paste = 'epaste'
    disable_pytest_capturing = False


class ConfigWithHighlight(ConfigTest):
    highlight = True


class PdbTest(pdb.Pdb):
    use_rawinput = 1

    def __init__(self, *args, **kwds):
        kwds.setdefault('Config', ConfigTest)
        pdb.Pdb.__init__(self, *args, **kwds)

    def _open_editor(self, editor, lineno, filename):
        print "RUN %s +%d '%s'" % (editor, lineno, filename)

    def _open_stdin_paste(self, cmd, lineno, filename, text):
        print "RUN %s +%d" % (cmd, lineno)
        print text


def set_trace(**kwds):
    frame = sys._getframe().f_back
    pdb.set_trace(frame, PdbTest, **kwds)

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
    cmds = []
    prompts = ('# ', '(#) ')
    for line in lines:
        for prompt in prompts:
            if line.startswith(prompt):
                cmds.append(line[len(prompt):])
                continue
    return cmds

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

def run_func(func, expected):
    """Runs given function and returns its output along with expected patterns.

    It does not make any assertions. To compare func's output with expected
    lines, use `check` function.
    """
    expected = expected.strip().splitlines()
    commands = extract_commands(expected)
    expected = map(cook_regexp, expected)
    return expected, runpdb(func, commands)

def check(func, expected):
    expected, lines = run_func(func, expected)
    maxlen = max(map(len, expected))
    ok = True
    print
    for pattern, string in map(None, expected, lines):
        ok = pattern is not None and string is not None and re.match(pattern, string)
        pattern = pattern or ''
        string = string or ''
        print pattern.ljust(maxlen+1), '| ', string,
        if ok:
            print
        else:
            print pdb.Color.set(pdb.Color.red, '    <<<<<')
    assert ok


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

def test_up_local_vars():
    def nested():
        set_trace()
        return
    def fn():
        xx = 42
        nested()

    check(fn, """
> .*nested()
-> return
# up
> .*fn()
-> nested()
# xx
42
# c
""")

def test_parseline():
    def fn():
        c = 42
        set_trace()
        return c

    check(fn, """
> .*fn()
-> return c
# c
42
# !c
42
# !!c
""")

def test_args_name():
    def fn():
        args = 42
        set_trace()
        return args

    check(fn, """
> .*fn()
-> return args
# args
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

def test_display():
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
# display a
# n
> .*fn()
-> a = 2
# n
> .*fn()
-> a = 3
a: 1 --> 2
# undisplay a
# n
> .*fn()
-> return a
# c
""")

def test_display_undefined():
    def fn():
        set_trace()
        b = 42
        return b

    check(fn, """
> .*fn()
-> b = 42
# display b
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


def test_sticky_by_default():
    class MyConfig(ConfigTest):
        sticky_by_default = True
    
    def fn():
        set_trace(Config=MyConfig)
        a = 1
        b = 2
        c = 3
        return a

    check(fn, """
> .*fn()
-> a = 1
CLEAR>.*

NUM         def fn():
NUM             set_trace(Config=MyConfig)
NUM  ->         a = 1
NUM             b = 2
NUM             c = 3
NUM             return a
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
-> return x
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
\*\* Error: .* is not a module, class, method, function, traceback, frame, or code object \*\*
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


def test_edit_obj():
    def fn():
        bar()
        set_trace()
        return 42
    def bar():
        pass
    _, bar_lineno = inspect.getsourcelines(bar)
    filename = os.path.abspath(__file__)
    if filename.endswith('.pyc'):
        filename = filename[:-1]

    check(fn, r"""
> .*fn()
-> return 42
# edit bar
RUN emacs \+%d '%s'
# c
""" % (bar_lineno, filename))

def test_edit_py_code_source():
    src = py.code.Source("""
    def bar():
        set_trace()
        return 42
    """)
    _, base_lineno = inspect.getsourcelines(test_edit_py_code_source)
    dic = {'set_trace': set_trace}
    exec src.compile() in dic # 8th line from the beginning of the function
    bar = dic['bar']
    src_compile_lineno = base_lineno + 8
    #
    filename = os.path.abspath(__file__)
    if filename.endswith('.pyc'):
        filename = filename[:-1]
    #
    check(bar, """
> .*bar()
-> return 42
# edit bar
RUN emacs \+%d '%s'
# c
""" % (src_compile_lineno, filename)) 


def test_put():
    def fn():
        set_trace()
        return 42
    _, lineno = inspect.getsourcelines(fn)
    start_lineno = lineno + 1

    check(fn, r"""
> .*fn()
-> return 42
# x = 10
# y = 12
# put
RUN epaste \+%d
        x = 10
        y = 12

# c
""" % start_lineno)

def test_paste():
    def g():
        print 'hello world'
    def fn():
        set_trace()
        if False: g()
        return 42
    _, lineno = inspect.getsourcelines(fn)
    start_lineno = lineno + 1

    check(fn, r"""
> .*fn()
-> if False: g()
# g()
hello world
# paste g()
hello world
RUN epaste \+%d
hello world

# c
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
> .*fn()
-> return x
# x = 10
# y = 12
# put
RUN epaste \+%d
            x = 10
            y = 12

# c
""" % start_lineno)

def test_side_effects_free():
    r = pdb.side_effects_free
    assert r.match('  x')
    assert r.match('x.y[12]')
    assert not r.match('x(10)')
    assert not r.match('  x = 10')
    assert not r.match('x = 10')

def test_put_side_effects_free():
    def fn():
        x = 10
        set_trace()
        return 42
    _, lineno = inspect.getsourcelines(fn)
    start_lineno = lineno + 2

    check(fn, r"""
> .*fn()
-> return 42
# x
10
# x.__add__
.*
# y = 12
# put
RUN epaste \+%d
        y = 12

# c
""" % start_lineno)

def test_enable_disable():
    def fn():
        x = 1
        pdb.disable()
        set_trace()
        x = 2
        pdb.enable()
        set_trace()
        return x

    check(fn, """
> .*fn()
-> return x
# x
2
# c
""")

def test_hideframe(): 
    @pdb.hideframe
    def g():
        pass
    assert g.func_code.co_consts[-1] is pdb._HIDE_FRAME

def test_hide_hidden_frames():
    @pdb.hideframe
    def g():
        set_trace()
        return 'foo'
    def fn():
        g()
        return 1

    check(fn, """
> .*fn()
-> g()
   1 frame hidden .*
# down           # cannot go down because the frame is hidden
... Newest frame
# hf_unhide
# down           # now the frame is no longer hidden
> .*g()
-> return 'foo'
# up
> .*fn()
-> g()
# hf_hide        # hide the frame again
# down
... Newest frame
# c
""")

def test_hide_current_frame():
    @pdb.hideframe
    def g():
        set_trace()
        return 'foo'
    def fn():
        g()
        return 1

    check(fn, """
> .*fn()
-> g()
   1 frame hidden .*
# hf_unhide
# down           # now the frame is no longer hidden
> .*g()
-> return 'foo'
# hf_hide        # hide the current frame, go to the top of the stack
> .*fn()
-> g()
# c
""")

def test_list_hidden_frames():
    @pdb.hideframe
    def g():
        set_trace()
        return 'foo'
    @pdb.hideframe
    def k():
        return g()
    def fn():
        k()
        return 1
    check(fn, """
> .*fn()
-> k()
   2 frames hidden .*
# hf_list
.*k()
-> return g()
.*g()
-> return 'foo'
# c
""")
    

def test_hidden_pytest_frames():
    def g():
        __tracebackhide__ = True
        set_trace()
        return 'foo'
    def k(g=g):
        return g()
    k = pdb.rebind_globals(k, {'__tracebackhide__': True})
    def fn():
        k()
        return 1

    check(fn, """
> .*fn()
-> k()
   2 frames hidden .*
# hf_list
.*k()
-> return g()
.*g()
-> return 'foo'
# c
    """)

def test_hidden_unittest_frames():
    
    def g(set_trace=set_trace):
        set_trace()
        return 'foo'
    g = pdb.rebind_globals(g, {'__unittest':True})
    def fn():
        return g()

    check(fn, """
> .*fn()
-> return g()
   1 frame hidden .*
# hf_list
.*g()
-> return 'foo'
# c
    """)

def test_break_on_setattr():
    # we don't use a class decorator to keep 2.5 compatibility
    class Foo(object):
        pass
    Foo = pdb.break_on_setattr('x', set_trace=set_trace)(Foo)
    def fn():
        obj = Foo()
        obj.x = 0
        return obj.x

    check(fn, """
> .*fn()
-> obj.x = 0
   1 frame hidden .*
# hasattr(obj, 'x')
False
# n
> .*fn()
-> return obj.x
# print obj.x
0
# c
""")

def test_break_on_setattr_condition():
    def mycond(obj, value):
        return value == 42
    # we don't use a class decorator to keep 2.5 compatibility
    class Foo(object):
        pass
    Foo = pdb.break_on_setattr('x', condition=mycond, set_trace=set_trace)(Foo)
    def fn():
        obj = Foo()
        obj.x = 0
        obj.x = 42
        return obj.x

    check(fn, """
> .*fn()
-> obj.x = 42
   1 frame hidden .*
# obj.x
0
# n
> .*fn()
-> return obj.x
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
        pdb.break_on_setattr('bar', condition=break_if_a, set_trace=set_trace)(Foo)
        b.bar = 10
        a.bar = 42

    check(fn, """
> .*fn()
-> a.bar = 42
   1 frame hidden .*
# c
""")

def test_break_on_setattr_overridden():
    # we don't use a class decorator to keep 2.5 compatibility
    class Foo(object):
        def __setattr__(self, attr, value):
            object.__setattr__(self, attr, value+1)
    Foo = pdb.break_on_setattr('x', set_trace=set_trace)(Foo)
    def fn():
        obj = Foo()
        obj.y = 41
        obj.x = 0
        return obj.x

    check(fn, """
> .*fn()
-> obj.x = 0
   1 frame hidden .*
# obj.y
42
# hasattr(obj, 'x')
False
# n
> .*fn()
-> return obj.x
# print obj.x
1
# c
""")

def test_track_with_no_args():
    def fn():
        set_trace()
        return 42

    check(fn, """
> .*fn()
-> return 42
# track
... SyntaxError:
# c
""")

def test_utf8():
    py.test.skip('fails on python 2.7')
    def fn():
        # тест
        a = 1
        set_trace(Config = ConfigWithHighlight)
        return a

    # we cannot easily use "check" because the output is full of ANSI escape
    # sequences
    expected, lines = run_func(fn, '# ll\n# c')
    assert 'тест' in lines[4]


def test_debug():
    def g():
        a = 1
        return a
    def fn():
        g()
        set_trace()
        return 1

    check(fn, """
> .*fn()
-> return 1
# debug g()
ENTERING RECURSIVE DEBUGGER
> .*
(#) s
--Call--
> .*g()
-> def g():
(#) ll
NUM  ->     def g():
NUM             a = 1
NUM             return a
(#) c
LEAVING RECURSIVE DEBUGGER
# c
""")

def test_before_interaction_hook():
    class MyConfig(ConfigTest):
        def before_interaction_hook(self, pdb):
            pdb.stdout.write('HOOK!\n')
    def fn():
        set_trace(Config=MyConfig)
        return 1

    check(fn, """
> .*fn()
-> return 1
HOOK!
# c
""")


def test_unicode_bug():
    def fn():
        set_trace()
        x = "this is plan ascii"
        y = "this contains a unicode: à"
        return

    check(fn, """
> .*fn()
-> x = "this is plan ascii"
# n
> .*fn()
-> y = "this contains a unicode:"
# c
""")
    

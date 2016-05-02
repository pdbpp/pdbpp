# -*- coding: utf-8 -*-
from __future__ import print_function

import inspect
import os.path
import sys
import re
from io import BytesIO
import py
import pytest

# make sure that we are really importing our pdb
sys.modules.pop('pdb', None)
import pdb

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

class ConfigTest(pdb.DefaultConfig):
    highlight = False
    use_pygments = False
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
        print("RUN %s +%d '%s'" % (editor, lineno, filename))

    def _open_stdin_paste(self, cmd, lineno, filename, text):
        print("RUN %s +%d" % (cmd, lineno))
        print(text)


def set_trace(cleanup=True, **kwds):
    if cleanup:
        pdb.cleanup()
    frame = sys._getframe().f_back
    pdb.set_trace(frame, PdbTest, **kwds)

def xpm():
    pdb.xpm(PdbTest)

def runpdb(func, input):
    oldstdin = sys.stdin
    oldstdout = sys.stdout

    if sys.version_info < (3, ):
        text_type = unicode
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
            return self.getvalue().decode(self.encoding)

    try:
        sys.stdin = FakeStdin(input)
        sys.stdout = stdout = MyBytesIO()
        func()
    finally:
        sys.stdin = oldstdin
        sys.stdout = oldstdout

    return stdout.get_unicode_value().splitlines()

def remove_comment(line): 
    if '###' in line:
        line, _ = line.split('###', 1)
    return line

def extract_commands(lines):
    cmds = []
    prompts = ('# ', '(#) ')
    for line in lines:
        line = remove_comment(line)
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
    ('NUM', ' *[0-9]*'),
    ('CLEAR', re.escape(pdb.CLEARSCREEN)),
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

def check(func, expected):
    expected, lines = run_func(func, expected)
    maxlen = max(map(len, expected))
    all_ok = True
    print()
    for pattern, string in zip(expected, lines):
        pattern = remove_comment(pattern)
        ok = pattern is not None and string is not None and re.match(pattern, string)
        pattern = pattern or ''
        string = string or ''
        print(pattern.ljust(maxlen+1), '| ', string, end='')
        if ok:
            print()
        else:
            print(pdb.Color.set(pdb.Color.red, '    <<<<<'))
            all_ok = False
    assert all_ok


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
# n
[NUM] > .*fn()
-> b = 2
# n
[NUM] > .*fn()
-> c = 3
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
# display a
# c
[NUM] > .*fn()
-> a = 3
a: 1 --> 2
# c
[NUM] > .*fn()
-> a = 4
a: 2 --> 3
# c
""")

def test_single_question_mark():
    def fn():
        def f2(x, y):
            """Return product of x and y"""
            return x * y
        set_trace()
        a = 1
        b = 2
        c = 3
        return a+b+c

    # import pdb; pdb.set_trace()
    check(fn, """
[NUM] > .*fn()
-> a = 1
# f2
<function .*f2 at .*>
# f2?
.*Type:.*function
.*String Form:.*<function .*f2 at .*>
.*File:.*test_pdb.py
.*Definition:.*f2(x, y)
.*Docstring:.*Return product of x and y
# c
""")

def test_double_question_mark():
    def fn():
        def f2(x, y):
            """Return product of x and y"""
            return x * y
        set_trace()
        a = 1
        b = 2
        c = 3
        return a+b+c

    check(fn, """
[NUM] > .*fn()
-> a = 1
# f2
<function .*f2 at .*>
# f2??
.*Type:.*function
.*String Form:.*<function .*f2 at .*>
.*File:.*test_pdb.py
.*Definition:.*f2(x, y)
.*Docstring:.*Return product of x and y
.*Source:.*
.* def f2(x, y):
.*     \"\"\"Return product of x and y\"\"\"
.*     return x \* y
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
[NUM] > .*nested()
-> return
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
# f {frame_num_a}
[NUM] > .*a()
-> b()
# c
""".format(frame_num_a=count_frames() + 2))

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
# up 3
[NUM] > .*runpdb()
-> func()
# down 1
[NUM] > .*a()
-> b()
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
# c
42
# !c
42
# r = 5
# r
5
# r = 6
# r
6
# !!c
""")

def test_args_name():
    def fn():
        args = 42
        set_trace()
        return args

    check(fn, """
[NUM] > .*fn()
-> return args
# args
42
# c
""")

def lineno():
    """Returns the current line number in our program."""
    return inspect.currentframe().f_back.f_lineno

@pytest.mark.parametrize("command,expected_regex", [
    ("", "Documented commands \(type help <topic>\):"),
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
    ("exec", "Execute the \(one-line\) statement"),

    ("hf_list", "\*\*\* No help"),
    ("paste", "\*\*\* No help"),
    ("put", "\*\*\* No help"),
    ("retval", "\*\*\* No help|return value"),
    ("rv", "\*\*\* No help|return value"),
    ("source", "\*\*\* No help"),
    ("unknown_command", "\*\*\* No help"),
    ("help", "print the list of available commands."),
])
def test_help(command, expected_regex):
    from pdb import StringIO
    instance = PdbTest()
    instance.stdout = StringIO()

    # Redirect sys.stdout because Python 2 pdb.py has `print >>self.stdout` for
    # some functions and plain ol' `print` for others.
    try:
        sys.stdout = instance.stdout
        instance.do_help(command)
    finally:
        sys.stdout == sys.__stdout__

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
# l {line_num}, 3
NUM  	    def fn():
NUM  	        a = 1
NUM  	        set_trace(Config=ConfigTest)
NUM  ->	        return a
# c
""".format(line_num=pdb.get_function_code(fn).co_firstlineno))

def test_longlist():
    def fn():
        a = 1
        set_trace()
        return a

    check(fn, """
[NUM] > .*fn()
-> return a
# ll
NUM         def fn():
NUM             a = 1
NUM             set_trace()
NUM  ->         return a
# c
""")

def test_display():
    def fn():
        a = 1
        set_trace()
        b = 1
        a = 2
        a = 3
        return a

    check(fn, """
[NUM] > .*fn()
-> b = 1
# display a
# n
[NUM] > .*fn()
-> a = 2
# n
[NUM] > .*fn()
-> a = 3
a: 1 --> 2
# undisplay a
# n
[NUM] > .*fn()
-> return a
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
# display b
# n
[NUM] > .*fn()
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
[NUM] > .*fn()
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
[NUM] > .*fn()
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
[NUM] > .*fn()
-> c = 3
# c
""")

def test_sticky_range():
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
[NUM] > .*fn()
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
[NUM] > .*fn()
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
Traceback (most recent call last):
.*File.*test_pdb.py.*, line NUM, in fn
    bar()
.*File.*test_pdb.py.*, line NUM, in bar
    assert False
AssertionError: assert False

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
""")


def test_postmortem_noargs():

    def fn():
        try:
            a = 1
            1/0
        except ZeroDivisionError:
            pdb.post_mortem(Pdb=PdbTest)

    check(fn, """
[NUM] > .*fn()
-> 1/0
# c
""")

def test_postmortem_needs_exceptioncontext():
    try:
        # py.test bug - doesnt clear the index error from finding the next item
        sys.exc_clear()
    except AttributeError:
        # Python 3 doesn't have sys.exc_clear
        pass
    py.test.raises(AssertionError, pdb.post_mortem, Pdb=PdbTest)

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
.*test_pdb.py.*, line NUM, in fn
.*for i in gen():
.*test_pdb.py.*, line NUM, in gen
.*assert False
AssertionError: assert False

[NUM] > .*gen()
-> assert False
# u
[NUM] > .*fn()
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
    
    exec(src.compile(), globals())
    check(fn, """
[NUM] > .*fn()
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
[NUM] > .*fn()
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
[NUM] > .*fn()
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
[NUM] > .*fn()
-> return 42
# edit
RUN emacs \+%d '%s'
# c
""" % (return42_lineno, filename))

    check(bar, r"""
[NUM] > .*fn()
-> return 42
# up
[NUM] > .*bar()
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
[NUM] > .*fn()
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
    exec(src.compile(), dic) # 8th line from the beginning of the function
    bar = dic['bar']
    src_compile_lineno = base_lineno + 8
    #
    filename = os.path.abspath(__file__)
    if filename.endswith('.pyc'):
        filename = filename[:-1]
    #
    check(bar, """
[NUM] > .*bar()
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
[NUM] > .*fn()
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
        print('hello world')
    def fn():
        set_trace()
        if 4 != 5: g()
        return 42
    _, lineno = inspect.getsourcelines(fn)
    start_lineno = lineno + 1

    check(fn, r"""
[NUM] > .*fn()
-> if 4 != 5: g()
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
[NUM] > .*fn()
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
[NUM] > .*fn()
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
[NUM] > .*fn()
-> return x
# x
2
# c
""")

def test_hideframe(): 
    @pdb.hideframe
    def g():
        pass
    assert pdb.get_function_code(g).co_consts[-1] is pdb._HIDE_FRAME

def test_hide_hidden_frames():
    @pdb.hideframe
    def g():
        set_trace()
        return 'foo'
    def fn():
        g()
        return 1

    check(fn, """
[NUM] > .*fn()
-> g()
   1 frame hidden .*
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
    @pdb.hideframe
    def g():
        set_trace()
        return 'foo'
    def fn():
        g()
        return 1

    check(fn, """
[NUM] > .*fn()
-> g()
   1 frame hidden .*
# hf_unhide
# down           ### now the frame is no longer hidden
[NUM] > .*g()
-> return 'foo'
# hf_hide        ### hide the current frame, go to the top of the stack
[NUM] > .*fn()
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
[NUM] > .*fn()
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
[NUM] > .*fn()
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
[NUM] > .*fn()
-> return g()
   1 frame hidden .*
# hf_list
.*g()
-> return 'foo'
# c
    """)

def test_dont_show_hidden_frames_count():
    class MyConfig(ConfigTest):
        show_hidden_frames_count = False

    @pdb.hideframe
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

    @pdb.hideframe
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
    Foo = pdb.break_on_setattr('x', set_trace=set_trace)(Foo)
    def fn():
        obj = Foo()
        obj.x = 0
        return obj.x

    check(fn, """
[NUM] > .*fn()
-> obj.x = 0
   1 frame hidden .*
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
[NUM] > .*fn()
-> obj.x = 42
   1 frame hidden .*
# obj.x
0
# n
[NUM] > .*fn()
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
[NUM] > .*fn()
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
[NUM] > .*fn()
-> obj.x = 0
   1 frame hidden .*
# obj.y
42
# hasattr(obj, 'x')
False
# n
[NUM] > .*fn()
-> return obj.x
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
[NUM] > .*fn()
-> return 1
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
HOOK!
# c
""")


def test_unicode_bug():
    def fn():
        set_trace()
        x = "this is plain ascii"
        y = "this contains a unicode: à"
        return

    check_output = """
[NUM] > .*fn()
-> x = "this is plain ascii"
# n
[NUM] > .*fn()
-> y = "this contains a unicode: à"
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
# c %d
Breakpoint 1 at .*/test_pdb.py:%d
Deleted breakpoint 1
[NUM] > .*fn()
-> z = 3
# c
""" % (line_z, line_z))

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
        x = 42
        b()
    def b():
        fn()
    def fn():
        set_trace()
        return

    check(a, """
[NUM] > .*fn()
-> return
# f {frame_num_a}
[NUM] > .*a()
-> b()
# p list(sorted(locals().keys()))
['b', 'x']
# c
""".format(frame_num_a=count_frames() + 2))

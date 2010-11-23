"""
Pdb++, a fancier version of pdb
===============================

This module extends the stdlib pdb in numerous ways, e.g. by providing
real completion of Python values instead of pdb's own commands, or by
adding few convenience commands like ``longlist``, ``interact`` or
``display``.

For a full explanation of each command, refer to the docstring or type
help <command> at the prompt.

Installation
------------

This module is meant to replace stdlib's pdb.py from the outsite;
simply put it in a directory in your PYTHONPATH, and you can start
using it immediately.  Since it's named pdb.py, every place which
imports pdb will now find the new module.

Dependencies
------------

To work properly, this module needs `rlcompleter_ng`_ to be installed.

To enable syntax highlighting, you must install `pygments`.

.. _pygments: http://pygments.org/
.. _`rlcompleter_ng`: http://codespeak.net/svn/user/antocuni/hack/rlcompleter_ng.py

To use the ``exec_if_unfocused`` option, you need to install ``wmctrl`` and my
`wmctrl.py` module.

.. _wmctrl.py: http://codespeak.net/svn/user/antocuni/hack/wmctrl.py

Configuration
-------------

To customize the configuration of Pdb++, you need to put a file named
.pdbrc.py in your home directory.  The file must contain a class named
``Config`` inheriting from ``DefaultConfig`` and overridding the
desired values.

To know which options are available, look at the comment in the
source.

You can find a sample configuration file, here:
http://codespeak.net/svn/user/antocuni/hack/pdbrc.py
"""

__version__='0.6'
__author__ ='Antonio Cuni <anto.cuni@gmail.com>'
__url__='http://bitbucket.org/antocuni/pdb'

import sys
import os.path
import inspect
import code
import types
import traceback
import subprocess
import re
from fancycompleter import Completer, ConfigurableClass, Color
import fancycompleter

# if it contains only _, digits, letters, [] or dots, it's probably side effects
# free
side_effects_free = re.compile(r'^ *[_0-9a-zA-Z\[\].]* *$')

def import_from_stdlib(name):
    import code # arbitrary module which stays in the same dir as pdb
    stdlibdir, _ = os.path.split(code.__file__)
    pyfile = os.path.join(stdlibdir, name + '.py')
    result = types.ModuleType(name)
    mydict = execfile(pyfile, result.__dict__)
    return result

pdb = import_from_stdlib('pdb')


class DefaultConfig:
    prompt = '(Pdb++) '
    completekey = 'tab'
    highlight = True
    bg = 'dark'
    use_pygments = True
    colorscheme = None
    editor = '${EDITOR:-vi}' # use $EDITOR if set, else default to vi
    stdin_paste = None       # for emacs, you can use my bin/epaste script
    truncate_long_lines = True
    exec_if_unfocused = None
    disable_pytest_capturing = True

    line_number_color = Color.turquoise
    current_line_color = 44 # blue

    def setup(self, pdb):
        pass

def setbgcolor(line, color):
    # hack hack hack
    # add a bgcolor attribute to all escape sequences found
    import re
    setbg = '\x1b[%dm' % color
    regexbg = '\\1;%dm' % color
    return setbg + re.sub('(\x1b\\[.*?)m', regexbg, line) + '\x1b[00m'

CLEARSCREEN = '\033[2J\033[1;1H'

def lasti2lineno(code, lasti):
    import dis
    linestarts = list(dis.findlinestarts(code))
    linestarts.reverse()
    for i, lineno in linestarts:
        if lasti >= i:
            return lineno
    return 0

class Undefined:
    def __repr__(self):
        return '<undefined>'
undefined = Undefined()

class Pdb(pdb.Pdb, ConfigurableClass):

    DefaultConfig = DefaultConfig
    config_filename = '.pdbrc.py'

    def __init__(self, *args, **kwds):
        Config = kwds.pop('Config', None)
        self.start_lineno = kwds.pop('start_lineno', None)
        self.start_filename = kwds.pop('start_filename', None)
        self.config = self.get_config(Config)
        self.config.setup(self)
        if self.config.disable_pytest_capturing:
            self._disable_pytest_capture_maybe()
        pdb.Pdb.__init__(self, *args, **kwds)
        self.prompt = self.config.prompt
        self.completekey = self.config.completekey

        self.mycompleter = None
        self.display_list = {} # frame --> (name --> last seen value)
        self.sticky = False
        self.sticky_ranges = {} # frame --> (start, end)
        self.tb_lineno = {} # frame --> lineno where the exception raised
        self.history = []
        self.show_hidden_frames = False
        self.hidden_frames = 0

    def _disable_pytest_capture_maybe(self):
        try:
            import py
        except ImportError:
            return
        try:
            capman = py.test.config.pluginmanager.getplugin('capturemanager')
        except KeyError:
            pass
        except AttributeError:
            pass # newer py.test with support ready
        else:
            capman.suspendcapture()

    def interaction(self, frame, traceback):
        if self.config.exec_if_unfocused:
            self.exec_if_unfocused()
        self.setup(frame, traceback)
        if self.hidden_frames:
            print "%d frames hidden" % self.hidden_frames
        self.print_stack_entry(self.stack[self.curindex])
        completer = fancycompleter.setup()
        completer.config.readline.set_completer(self.complete)
        self.cmdloop()
        self.forget()

    def exec_if_unfocused(self):
        import os
        import wmctrl
        try:
            winid = int(os.getenv('WINDOWID'))
        except (TypeError, ValueError):
            return # cannot find WINDOWID of the terminal
        active_win = wmctrl.Window.get_active()
        if not active_win or (int(active_win.id, 16) != winid):
            os.system(self.config.exec_if_unfocused)

    def setup(self, frame, tb):
        pdb.Pdb.setup(self, frame, tb)
        while tb:
            lineno = lasti2lineno(tb.tb_frame.f_code, tb.tb_lasti)
            self.tb_lineno[tb.tb_frame] = lineno
            tb = tb.tb_next

    def _is_hidden(self, frame):
        consts = frame.f_code.co_consts
        return consts and consts[-1] is _HIDE_FRAME

    def get_stack(self, f, t):
        # show all the frames, except the ones that explicitly ask to be hidden
        stack, i = pdb.Pdb.get_stack(self, f, t)
        self.hidden_frames = 0
        newstack = []
        for frame, lineno in stack:
            if self._is_hidden(frame) and not self.show_hidden_frames:
                self.hidden_frames += 1
            else:
                newstack.append((frame, lineno))
        stack = newstack
        i = max(0, len(stack) - 1)
        return stack, i

    def forget(self):
        pdb.Pdb.forget(self)
        self.raise_lineno = {}

    def complete(self, text, state):
        if state == 0:
            mydict = self.curframe.f_globals.copy()
            mydict.update(self.curframe.f_locals)
            self.mycompleter = Completer(mydict)
        return self.mycompleter.complete(text, state)

    def _init_pygments(self): 
        if not self.config.use_pygments:
            return False
        try:
            from pygments.lexers import PythonLexer
            from pygments.formatters import TerminalFormatter
        except ImportError:
            return False

        if hasattr(self, '_fmt'):
            return True
        
        self._fmt = TerminalFormatter(bg=self.config.bg,
                                      colorscheme=self.config.colorscheme)
        self._lexer = PythonLexer()
        return True

    def format_source(self, src):
        if not self._init_pygments():
            return src
        from pygments import highlight, lex
        return highlight(src, self._lexer, self._fmt)

    def format_line(self, lineno, marker, line):
        lineno = '%4d' % lineno
        if self.config.highlight:
            lineno = Color.set(self.config.line_number_color, lineno)
        line = '%s  %2s %s' % (lineno, marker, line)
        if self.config.highlight and marker == '->':
            line = setbgcolor(line, self.config.current_line_color)
        return line

    def parseline(self, line):
        cmd, arg, newline = pdb.Pdb.parseline(self, line)
        # don't execute short disruptive commands if a variable with
        # the name exits in the current contex; this prevents pdb to
        # quit if you type e.g. 'r[0]' by mystake.
        if cmd in ['a', 'b', 'c', 'r', 'q', 'args'] and (cmd in self.curframe.f_globals or
                                               cmd in self.curframe.f_locals):
            line = '!' + line
            return pdb.Pdb.parseline(self, line)
        return cmd, arg, newline

    def default(self, line):
        self.history.append(line)
        return pdb.Pdb.default(self, line)

    def do_longlist(self, arg):
        """
        {longlist|ll}
        List source code for the current function.
        
        Differently than list, the whole function is displayed; the
        current line is marked with '->'.  In case of post-mortem
        debugging, the line which effectively raised the exception is
        marked with '>>'.

        If the 'highlight' config option is set and pygments is
        installed, the source code is colorized.
        """
        self.lastcmd = 'longlist'
        self._printlonglist()

    def _printlonglist(self, linerange=None):
        try:
            if self.curframe.f_code.co_name == '<module>':
                # inspect.getsourcelines is buggy in this case: if we just
                # pass the frame, it returns the source for the first function
                # defined in the module.  Instead, we want the full source
                # code of the module
                lines, _ = inspect.findsource(self.curframe)
                lineno = 1
            else:
                lines, lineno = inspect.getsourcelines(self.curframe)
        except IOError, e:
            print '** Error: %s **' % e
            return
        if linerange:
            start, end = linerange
            start = max(start, lineno)
            end = min(end, lineno+len(lines))
            lines = lines[start-lineno:end-lineno]
            lineno = start
        self._print_lines(lines, lineno)

    def _print_lines(self, lines, lineno, print_markers=True):
        exc_lineno = self.tb_lineno.get(self.curframe, None)
        lines = [line[:-1] for line in lines] # remove the trailing '\n'
        width, height = self.get_terminal_size()

        if self.config.truncate_long_lines:
            maxlength = max(width - 9, 16)
            lines = [line[:maxlength] for line in lines]
        else:
            maxlength = max(map(len, lines))

        if self.config.highlight:
            lines = [line.ljust(maxlength) for line in lines]
            src = self.format_source('\n'.join(lines))
            lines = src.splitlines()
        if height >= 6:
            last_marker_line = max(self.curframe.f_lineno, exc_lineno) - lineno
            if last_marker_line >= 0:
                maxlines = last_marker_line + height * 2 // 3
                if len(lines) > maxlines:
                    lines = lines[:maxlines]
                    lines.append('...')
        for i, line in enumerate(lines):
            marker = ''
            if lineno == self.curframe.f_lineno and print_markers:
                marker = '->'
            elif lineno == exc_lineno and print_markers:
                marker = '>>'
            lines[i] = self.format_line(lineno, marker, line)
            lineno += 1
        print '\n'.join(lines)

    do_ll = do_longlist

    def do_list(self, arg):
        from StringIO import StringIO
        oldstdout = sys.stdout
        sys.stdout = StringIO()
        pdb.Pdb.do_list(self, arg)
        src = self.format_source(sys.stdout.getvalue())
        sys.stdout = oldstdout
        print src,

    do_l = do_list

    def do_interact(self, arg):
        """
        interact

        Start an interative interpreter whose global namespace
        contains all the names found in the current scope.
        """
        ns = self.curframe.f_globals.copy()
        ns.update(self.curframe.f_locals)
        code.interact("*interactive*", local=ns)

    def do_track(self, arg):
        """
        track expression

        Display a graph showing which objects are referred by the
        value of the expression.  This command requires pypy to be in
        the current PYTHONPATH.
        """
        try:
            from pypy.translator.tool.reftracker import track
        except ImportError:
            print '** cannot import pypy.translator.tool.reftracker **'
            return
        val = self._getval(arg)
        track(val)

    def _get_display_list(self):
        return self.display_list.setdefault(self.curframe, {})

    def _getval_or_undefined(self, arg):
        try:
            return eval(arg, self.curframe.f_globals,
                        self.curframe.f_locals)
        except NameError:
            return undefined

    def do_display(self, arg):
        """
        display expression

        Add expression to the display list; expressions in this list
        are evaluated at each step, and printed every time its value
        changes.

        WARNING: since the expressions is evaluated multiple time, pay
        attention not to put expressions with side-effects in the
        display list.
        """
        try:
            value = self._getval_or_undefined(arg)
        except:
            return
        self._get_display_list()[arg] = value

    def do_undisplay(self, arg):
        """
        undisplay expression

        Remove expression from the display list.
        """
        try:
            del self._get_display_list()[arg]
        except KeyError:
            print '** %s not in the display list **' % arg

    def _print_if_sticky(self):
        if self.sticky:
            sys.stdout.write(CLEARSCREEN)
            frame, lineno = self.stack[self.curindex]
            filename = self.canonic(frame.f_code.co_filename)
            s = '> %s(%r)' % (filename, lineno)
            print s
            print
            sticky_range = self.sticky_ranges.get(self.curframe, None)
            self._printlonglist(sticky_range)

            if '__exception__' in frame.f_locals:
                exc = frame.f_locals['__exception__']
                if len(exc) == 2:
                    exc_type, exc_value = exc
                    s = ''
                    try:
                        try:
                            s = exc_type.__name__
                        except AttributeError:
                            s = str(exc_type)
                        if exc_value is not None:
                            s += ': '
                            s += str(exc_value)
                    except KeyboardInterrupt:
                        raise
                    except:
                        s += '(unprintable exception)'
                    print Color.set(self.config.line_number_color, ' ' + s)
                    return
            if '__return__' in frame.f_locals:
                rv = frame.f_locals['__return__']
                try:
                    s = repr(rv)
                except KeyboardInterrupt:
                    raise
                except:
                    s = '(unprintable return value)'
                print Color.set(self.config.line_number_color, ' return ' + s)

    def do_sticky(self, arg):
        """
        sticky [start end]

        Toggle sticky mode. When in sticky mode, it clear the screen
        and longlist the current functions, making the source
        appearing always in the same position. Useful to follow the
        flow control of a function when doing step-by-step execution.

        If ``start`` and ``end`` are given, sticky mode is enabled and
        only lines within that range (extremes included) will be
        displayed.
        """
        if arg:
            try:
                start, end = map(int, arg.split())
            except ValueError:
                print '** Error when parsing argument: %s **' % arg
                return
            self.sticky = True
            self.sticky_ranges[self.curframe] = start, end+1
        else:
            self.sticky = not self.sticky
            self.sticky_range = None
        self._print_if_sticky()

    def print_current_stack_entry(self):
        if self.sticky:
            self._print_if_sticky()
        else:
            self.print_stack_entry(self.stack[self.curindex])

    def preloop(self):
        self._print_if_sticky()
        display_list = self._get_display_list()
        for expr, oldvalue in display_list.iteritems():
            newvalue = self._getval_or_undefined(expr)
            # check for identity first; this prevents custom __eq__ to
            # be called at every loop, and also prevents instances
            # whose fields are changed to be displayed
            if newvalue is not oldvalue or newvalue != oldvalue:
                display_list[expr] = newvalue
                print '%s: %r --> %r' % (expr, oldvalue, newvalue)


    def _get_position_of_arg(self, arg):
        try:
            obj = self._getval(arg)
        except:
            return None, None, None
        try:
            filename = inspect.getabsfile(obj)
            lines, lineno = inspect.getsourcelines(obj)
        except (IOError, TypeError), e:
            print '** Error: %s **' % e
            return None, None, None
        return filename, lineno, lines

    def do_source(self, arg):
        _, lineno, lines = self._get_position_of_arg(arg)
        if lineno is None:
            return
        self._print_lines(lines, lineno, print_markers=False)

    def do_up(self, arg):
        if self.curindex == 0:
            print '*** Oldest frame'
        else:
            self.curindex = self.curindex - 1
            self.curframe = self.stack[self.curindex][0]
            self.print_current_stack_entry()
            self.lineno = None
    do_u = do_up

    def do_down(self, arg):
        if self.curindex + 1 == len(self.stack):
            print '*** Newest frame'
        else:
            self.curindex = self.curindex + 1
            self.curframe = self.stack[self.curindex][0]
            self.print_current_stack_entry()
            self.lineno = None
    do_d = do_down

    def get_terminal_size(self):
        try:
            import termios, fcntl, struct
            call = fcntl.ioctl(0, termios.TIOCGWINSZ, "\x00"*8)
            height, width = struct.unpack("hhhh", call)[:2]
        except (SystemExit, KeyboardInterrupt), e:
            raise
        except:
            width = int(os.environ.get('COLUMNS', 80))
            height = int(os.environ.get('COLUMNS', 24))
        return width, height

    def _open_editor(self, editor, lineno, filename):
        filename = filename.replace("'", "\\'")
        os.system("%s +%d '%s'" % (editor, lineno, filename))

    def _get_current_position(self):
        frame = self.curframe
        lineno = frame.f_lineno
        filename = os.path.abspath(frame.f_code.co_filename)
        return filename, lineno

    def do_edit(self, arg):
        "Open an editor visiting the current file at the current line"
        if arg == '':
            filename, lineno = self._get_current_position()
        else:
            filename, lineno, _ = self._get_position_of_arg(arg)
            if filename is None:
                return
            # this case handles code generated with py.code.Source()
            # filename is something like '<0-codegen foo.py:18>'
            match = re.match(r'.*<\d+-codegen (.*):(\d+)>', filename)
            if match:
                filename = match.group(1)
                lineno = int(match.group(2))
        editor = self.config.editor
        self._open_editor(editor, lineno, filename)

    do_ed = do_edit

    def _get_history(self):
        return [s for s in self.history if not side_effects_free.match(s)]

    def _get_history_text(self):
        import linecache
        line = linecache.getline(self.start_filename, self.start_lineno)
        nspaces = len(line) - len(line.lstrip())
        indent = ' ' * nspaces
        history = [indent + s for s in self._get_history()]
        return '\n'.join(history) + '\n'

    def _open_stdin_paste(self, stdin_paste, lineno, filename, text):
        proc = subprocess.Popen([stdin_paste, '+%d' % lineno, filename],
                                stdin = subprocess.PIPE)
        proc.stdin.write(text)
        proc.stdin.close()

    def do_put(self, arg):
        stdin_paste = self.config.stdin_paste
        if stdin_paste is None:
            print '** Error: the "stdin_paste" option is not configure **'
        filename = self.start_filename
        lineno = self.start_lineno
        text = self._get_history_text()
        self._open_stdin_paste(stdin_paste, lineno, filename, text)


# simplified interface

if hasattr(pdb, 'Restart'):
    Restart = pdb.Restart

# copy some functions from pdb.py, but rebind the global dictionary
for name in 'run runeval runctx runcall pm main'.split():
    func = getattr(pdb, name)
    newfunc = types.FunctionType(func.func_code, globals(), func.func_name)
    globals()[name] = newfunc
del name, func, newfunc

def post_mortem(t, Pdb=Pdb):
    p = Pdb()
    p.reset()
    p.interaction(None, t)

def set_trace(frame=None, Pdb=Pdb):
    if frame is None:
        frame = sys._getframe().f_back
    filename = frame.f_code.co_filename
    lineno = frame.f_lineno
    Pdb(start_lineno=lineno, start_filename=filename).set_trace(frame)

# pdb++ specific interface

def xpm(Pdb=Pdb):
    """
    To be used inside an except clause, enter a post-mortem pdb
    related to the just catched exception.
    """
    post_mortem(sys.exc_info()[2], Pdb)

def enable():
    global set_trace
    set_trace = enable.set_trace
enable.set_trace = set_trace

def disable():
    global set_trace
    set_trace = disable.set_trace
disable.set_trace = lambda frame=None, Pdb=Pdb: None

def set_tracex():
    print 'PDB!'
set_tracex._dont_inline_ = True

_HIDE_FRAME = object()

def hideframe(func):
    import new
    c = func.func_code
    c = new.code(c.co_argcount, c.co_nlocals, c.co_stacksize,
                 c.co_flags, c.co_code,
                 c.co_consts+(_HIDE_FRAME,),
                 c.co_names, c.co_varnames, c.co_filename,
                 c.co_name, c.co_firstlineno, c.co_lnotab,
                 c.co_freevars, c.co_cellvars)
    func.func_code = c
    return func


def always(obj, value):
    return True

def break_on_setattr(attrname, condition=always, set_trace=set_trace):
    def decorator(cls):
        old___setattr__ = cls.__setattr__
        @hideframe
        def __setattr__(self, attr, value):
            if attr == attrname and condition(self, value):
                set_trace()
            old___setattr__(self, attr, value)
        assert '__setattr__' not in cls.__dict__
        cls.__setattr__ = __setattr__
        return cls
    return decorator

if __name__=='__main__':
    import pdb
    pdb.main()


# XXX todo:
# get_terminal_size inside emacs returns 0,0
# copy func_defaults when "cloning" functions, else you can't use
# e.g. pdb.run(code)

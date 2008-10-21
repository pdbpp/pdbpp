"""
Pdb++, a fancier version of pdb
===============================

This module extends the stdlib pdb in numerous ways, e.g. by providing
real completion of Python values instead of pdb's own commands, or by
adding few convenience commands like ``longlist``, ``interact`` or
``watch``.

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

__version__='0.1'
__author__ ='Antonio Cuni <anto.cuni@gmail.com>'
__url__='http://codespeak.net/svn/user/antocuni/hack/pdb.py'

import sys
import os.path
import inspect
import code
import types
from rlcompleter_ng import Completer, ConfigurableClass, setcolor, colors


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
    colorscheme = None
    editor = '${EDITOR:-vi}' # use $EDITOR if set, else default to vi
    truncate_long_lines = True

    line_number_color = colors.turquoise
    current_line_color = 44 # blue

    def setup(self, pdb):
        pass

def getsourcelines(obj):
    try:
        return inspect.getsourcelines(obj)
    except IOError:
        pass

    if isinstance(obj, types.FrameType):
        filename = obj.f_code.co_filename
        if hasattr(filename, '__source__'):
            first = max(1, obj.f_lineno - 5)
            lines = [line + '\n' for line in filename.__source__.lines]
            return lines, first
    raise IOError('could not get source code')

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
    assert False, 'Invalid instruction number: %s' % lasti

class Undefined:
    def __repr__(self):
        return '<undefined>'
undefined = Undefined()

class Pdb(pdb.Pdb, ConfigurableClass):

    DefaultConfig = DefaultConfig
    config_filename = '.pdbrc.py'

    def __init__(self, *args, **kwds):
        Config = kwds.pop('Config', None)
        pdb.Pdb.__init__(self, *args, **kwds)
        self.config = self.get_config(Config)
        self.config.setup(self)
        self.prompt = self.config.prompt
        self.completekey = self.config.completekey

        self.mycompleter = None
        self.watching = {} # frame --> (name --> last seen value)
        self.sticky = False
        self.sticky_ranges = {} # frame --> (start, end)
        self.tb_lineno = {} # frame --> lineno where the exception raised

    def interaction(self, frame, traceback):
        self.setup(frame, traceback)
        self.print_stack_entry(self.stack[self.curindex])
        self.cmdloop()
        self.forget()

    def setup(self, frame, tb):
        pdb.Pdb.setup(self, frame, tb)
        while tb:
            lineno = lasti2lineno(tb.tb_frame.f_code, tb.tb_lasti)
            self.tb_lineno[tb.tb_frame] = lineno
            tb = tb.tb_next

    def get_stack(self, f, t):
        # Modified from bdb.py to be able to walk the stack beyond generators,
        # which does not work in the normal pdb :-(
        stack, i = pdb.Pdb.get_stack(self, f, t)
        if f is None:
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
            lineno = setcolor(lineno, self.config.line_number_color)
        line = '%s  %2s %s' % (lineno, marker, line)
        if self.config.highlight and marker == '->':
            line = setbgcolor(line, self.config.current_line_color)
        return line

    def parseline(self, line):
        cmd, arg, newline = pdb.Pdb.parseline(self, line)
        # don't execute short disruptive commands if a variable with
        # the name exits in the current contex; this prevents pdb to
        # quit if you type e.g. 'r[0]' by mystake.
        if cmd in ['c', 'r', 'q'] and (cmd in self.curframe.f_globals or
                                       cmd in self.curframe.f_locals):
            line = '!' + line
            return pdb.Pdb.parseline(self, line)
        return cmd, arg, newline

    def do_longlist(self, arg):
        """
        {longlist|ll}
        List source code for the current function.
        
        Differently that list, the whole function is displayed; the
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
            lines, lineno = getsourcelines(self.curframe)
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

    def _get_watching(self):
        return self.watching.setdefault(self.curframe, {})

    def _getval_or_undefined(self, arg):
        try:
            return eval(arg, self.curframe.f_globals,
                        self.curframe.f_locals)
        except NameError:
            return undefined

    def do_watch(self, arg):
        """
        watch expression

        Add expression to the watching list; expressions in this list
        are evaluated at each step, and printed every time its value
        changes.

        WARNING: since the expressions is evaluated multiple time, pay
        attention not to put expressions with side-effects in the
        watching list.
        """
        try:
            value = self._getval_or_undefined(arg)
        except:
            return
        self._get_watching()[arg] = value

    def do_unwatch(self, arg):
        """
        unwatch expression

        Remove expression from the watching list.
        """
        try:
            del self._get_watching()[arg]
        except KeyError:
            print '** not watching %s **' % arg

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
                    print setcolor(' ' + s, self.config.line_number_color)
                    return
            if '__return__' in frame.f_locals:
                rv = frame.f_locals['__return__']
                try:
                    s = repr(rv)
                except KeyboardInterrupt:
                    raise
                except:
                    s = '(unprintable return value)'
                print setcolor(' return ' + s, self.config.line_number_color)

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
        watching = self._get_watching()
        for expr, oldvalue in watching.iteritems():
            newvalue = self._getval_or_undefined(expr)
            # check for identity first; this prevents custom __eq__ to
            # be called at every loop, and also prevents instances
            # whose fields are changed to be displayed
            if newvalue is not oldvalue or newvalue != oldvalue:
                watching[expr] = newvalue
                print '%s: %r --> %r' % (expr, oldvalue, newvalue)

    def do_source(self, arg):
        try:
            obj = self._getval(arg)
        except:
            return
        try:
            lines, lineno = getsourcelines(obj)
        except (IOError, TypeError), e:
            print '** Error: %s **' % e
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
        os.system("%s +%d '%s'" % (editor, lineno, filename))
        
    def do_edit(self, arg):
        "Open an editor visiting the current file at the current line"
        editor = self.config.editor
        frame = self.curframe
        lineno = frame.f_lineno
        filename = os.path.abspath(frame.f_code.co_filename)
        filename = filename.replace("'", "\\'")
        self._open_editor(editor, lineno, filename)

    do_ed = do_edit

# Simplified interface

# copy some functions from pdb.py, but rebind the global dictionary
for name in 'run runeval runctx runcall set_trace pm'.split():
    func = getattr(pdb, name)
    newfunc = types.FunctionType(func.func_code, globals(), func.func_name)
    globals()[name] = newfunc
del name, func, newfunc

def post_mortem(t, Pdb=Pdb):
    p = Pdb()
    p.reset()
    p.interaction(None, t)

# pdb++ specific interface

def xpm(Pdb=Pdb):
    """
    To be used inside an except clause, enter a post-mortem pdb
    related to the just catched exception.
    """
    post_mortem(sys.exc_info()[2], Pdb)

def set_tracex():
    print 'PDB!'

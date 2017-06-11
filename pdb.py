"""
pdb++, a drop-in replacement for pdb
====================================

This module extends the stdlib pdb in numerous ways: look at the README for
more details on pdb++ features.
"""

from __future__ import print_function

def get_version():
    try:
        import pkg_resources
        dist = pkg_resources.get_distribution('pdbpp')
        return dist.version
    except Exception:
        pass


__version__ = get_version()
__author__ = 'Antonio Cuni <anto.cuni@gmail.com>'
__url__ = 'http://github.com/antocuni/pdb'

import sys
import os.path
import inspect
import code
import codecs
import types
import traceback
import subprocess
import pprint
import re
from fancycompleter import Completer, ConfigurableClass, Color
import fancycompleter

try:
    from inspect import signature  # Python >= 3.3
except ImportError:
    try:
        from funcsigs import signature
    except ImportError:
        def signature(obj):
            return ' [pip install funcsigs to show the signature]'

try:
    from collections import OrderedDict
except ImportError:
    from ordereddict import OrderedDict

# if it contains only _, digits, letters, [] or dots, it's probably side effects
# free
side_effects_free = re.compile(r'^ *[_0-9a-zA-Z\[\].]* *$')

try:
    if sys.version_info < (3, ):
        from io import BytesIO as StringIO
    else:
        from io import StringIO
except ImportError:
    try:
        from cStringIO import StringIO
    except ImportError:
        from StringIO import StringIO


def get_function_name(func):
    if sys.version_info >= (2, 6):
        return func.__name__
    else:
        return func.func_name


def get_function_code(func):
    if sys.version_info >= (2, 6):
        return func.__code__
    else:
        return func.func_code


def set_function_code(func, code_obj):
    if sys.version_info >= (2, 6):
        func.__code__ = code_obj
    else:
        func.func_code = code_obj


def get_function_defaults(func):
    if sys.version_info >= (2, 6):
        return func.__defaults__
    else:
        return func.func_defaults


def import_from_stdlib(name):
    import code  # arbitrary module which stays in the same dir as pdb
    result = types.ModuleType(name)
    imported = False

    if hasattr(code, '__loader__'):
        try:
            code = code.__loader__.get_code('pdb')
        except ImportError:
            pass
        else:
            resultd = {}
            exec(code, resultd)
            for k, v in resultd.items():
              setattr(result, k, v)
            imported = True

    if not imported:
        stdlibdir, _ = os.path.split(code.__file__)
        pyfile = os.path.join(stdlibdir, name + '.py')
        with open(pyfile) as f:
            src = f.read()
        co_module = compile(src, pyfile, 'exec', dont_inherit=True)
        exec(co_module, result.__dict__)

    return result

pdb = import_from_stdlib('pdb')

def rebind_globals(func, newglobals=None):
    if newglobals is None:
        newglobals = globals()
    newfunc = types.FunctionType(func.__code__, newglobals, func.__name__,
                                 func.__defaults__)
    return newfunc


class DefaultConfig:
    prompt = '(Pdb++) '
    highlight = True
    sticky_by_default = False
    bg = 'dark'
    use_pygments = True
    colorscheme = None
    use_terminal256formatter = False
    editor = '${EDITOR:-vi}' # use $EDITOR if set, else default to vi
    stdin_paste = None       # for emacs, you can use my bin/epaste script
    truncate_long_lines = True
    exec_if_unfocused = None
    disable_pytest_capturing = False
    encodings = ('utf-8', 'latin-1')
    enable_hidden_frames = True
    show_hidden_frames_count = True

    line_number_color = Color.turquoise
    filename_color = Color.yellow
    current_line_color = 44 # blue

    def setup(self, pdb):
        pass

    def before_interaction_hook(self, pdb):
        pass

def setbgcolor(line, color):
    # hack hack hack
    # add a bgcolor attribute to all escape sequences found
    import re
    setbg = '\x1b[%dm' % color
    regexbg = '\\1;%dm' % color
    result = setbg + re.sub('(\x1b\\[.*?)m', regexbg, line) + '\x1b[00m'
    if os.environ.get('TERM') == 'eterm-color':
        # it seems that emacs' terminal has problems with some ANSI escape
        # sequences. Eg, 'ESC[44m' sets the background color in all terminals
        # I tried, but not in emacs. To set the background color, it needs to
        # have also an explicit foreground color, e.g. 'ESC[37;44m'. These
        # three lines are a hack, they try to add a foreground color to all
        # escape sequences wich are not recognized by emacs. However, we need
        # to pick one specific fg color: I choose white (==37), but you might
        # want to change it.  These lines seems to work fine with the ANSI
        # codes produced by pygments, but they are surely not a general
        # solution.
        result = result.replace(setbg, '\x1b[37;%dm' % color)
        result = result.replace('\x1b[00;%dm' % color, '\x1b[37;%dm' % color)
        result = result.replace('\x1b[39;49;00;', '\x1b[37;')
    return result

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
        self.ConfigFactory = kwds.pop('Config', None)
        self.start_lineno = kwds.pop('start_lineno', None)
        self.start_filename = kwds.pop('start_filename', None)
        self.config = self.get_config(self.ConfigFactory)
        self.config.setup(self)
        if self.config.disable_pytest_capturing:
            self._disable_pytest_capture_maybe()
        pdb.Pdb.__init__(self, *args, **kwds)
        self.prompt = self.config.prompt

        self.mycompleter = None
        self.display_list = {} # frame --> (name --> last seen value)
        self.sticky = self.config.sticky_by_default
        self.sticky_ranges = {} # frame --> (start, end)
        self.tb_lineno = {} # frame --> lineno where the exception raised
        self.history = []
        self.show_hidden_frames = False
        self.hidden_frames = []
        self.stdout = self.ensure_file_can_write_unicode(self.stdout)

    def ensure_file_can_write_unicode(self, f):
        # Wrap with an encoder, but only if not already wrapped
        if not hasattr(f, 'stream') and \
           getattr(f, 'encoding', False) and f.encoding.lower() != 'utf-8':
            f = codecs.getwriter('utf-8')(f)

        return f

    def _disable_pytest_capture_maybe(self):
        try:
            import py.test
            py.test.config # force to raise ImportError if pytest is not
                           # installed
        except (ImportError, AttributeError):
            return
        try:
            capman = py.test.config.pluginmanager.getplugin('capturemanager')
            capman.suspendcapture()
        except KeyError:
            pass
        except AttributeError:
            pass # newer py.test with support ready, or very old py.test for
                 # which this hack does not work

    def interaction(self, frame, traceback):
        if self.config.exec_if_unfocused:
            self.exec_if_unfocused()
        self.setup(frame, traceback)
        self.print_stack_entry(self.stack[self.curindex])
        self.print_hidden_frames_count()
        completer = fancycompleter.setup()
        old_completer = completer.config.readline.get_completer()
        completer.config.readline.set_completer(self.complete)
        self.config.before_interaction_hook(self)
        self.cmdloop()
        completer.config.readline.set_completer(old_completer)
        self.forget()

    def print_hidden_frames_count(self):
        n = len(self.hidden_frames)
        if n and self.config.show_hidden_frames_count:
            plural = n>1 and 's' or ''
            print("   %d frame%s hidden (try 'help hidden_frames')" % (n, plural),
                  file=self.stdout)

    def exec_if_unfocused(self):
        import os
        import wmctrl
        term = os.getenv('TERM', '')
        try:
            winid = int(os.getenv('WINDOWID'))
        except (TypeError, ValueError):
            return # cannot find WINDOWID of the terminal
        active_win = wmctrl.Window.get_active()
        if not active_win or (int(active_win.id, 16) != winid) and \
           not (active_win.wm_class == 'emacs.Emacs' and term.startswith('eterm')):
            os.system(self.config.exec_if_unfocused)

    def setup(self, frame, tb):
        pdb.Pdb.setup(self, frame, tb)
        while tb:
            lineno = lasti2lineno(tb.tb_frame.f_code, tb.tb_lasti)
            self.tb_lineno[tb.tb_frame] = lineno
            tb = tb.tb_next

    def _is_hidden(self, frame):
        if not self.config.enable_hidden_frames:
            return False
        consts = frame.f_code.co_consts
        if consts and consts[-1] is _HIDE_FRAME:
            return True
        if frame.f_globals.get('__unittest'):
            return True
        if frame.f_locals.get('__tracebackhide__') \
           or frame.f_globals.get('__tracebackhide__'):
            return True

    def get_stack(self, f, t):
        # show all the frames, except the ones that explicitly ask to be hidden
        fullstack, _ = pdb.Pdb.get_stack(self, f, t)
        self.fullstack = fullstack
        return self.compute_stack(fullstack)

    def compute_stack(self, fullstack):
        self.hidden_frames = []
        newstack = []
        for frame, lineno in fullstack:
            if self._is_hidden(frame) and not self.show_hidden_frames:
                self.hidden_frames.append((frame, lineno))
            else:
                newstack.append((frame, lineno))
        stack = newstack
        i = max(0, len(stack) - 1)
        return stack, i

    def refresh_stack(self):
        """
        Recompute the stack after e.g. show_hidden_frames has been modified
        """
        self.stack, _ = self.compute_stack(self.fullstack)
        # find the current frame in the new stack
        for i, (frame, _) in enumerate(self.stack):
            if frame is self.curframe:
                self.curindex = i
                break
        else:
            self.curindex = len(self.stack)-1
            self.curframe = self.stack[-1][0]
            self.print_current_stack_entry()

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
            from pygments.formatters import TerminalFormatter, Terminal256Formatter
        except ImportError:
            return False

        if hasattr(self, '_fmt'):
            return True
        if hasattr(self.config, 'formatter'):
            self._fmt = self.config.formatter
        else:
            Formatter = (Terminal256Formatter
                         if self.config.use_terminal256formatter
                            and '256color' in os.environ.get('TERM', '')
                         else TerminalFormatter)
            self._fmt = Formatter(bg=self.config.bg,
                                  colorscheme=self.config.colorscheme)
        self._lexer = PythonLexer()
        return True


    stack_entry_regexp = re.compile(r'(.*?)\(([0-9]+?)\)(.*)', re.DOTALL)
    #
    def format_stack_entry(self, frame_lineno, lprefix=': '):
        entry = pdb.Pdb.format_stack_entry(self, frame_lineno, lprefix)
        entry = self.try_to_decode(entry)
        if self.config.highlight:
            match = self.stack_entry_regexp.match(entry)
            if match:
                filename, lineno, other = match.groups()
                filename = Color.set(self.config.filename_color, filename)
                lineno = Color.set(self.config.line_number_color, lineno)
                entry = '%s(%s)%s' % (filename, lineno, other)
        return entry

    def try_to_decode(self, s):
        for encoding in self.config.encodings:
            try:
                return s.decode(encoding)
            except (UnicodeDecodeError, AttributeError):
                pass
        return s

    def format_source(self, src):
        if not self._init_pygments():
            return src
        from pygments import highlight
        src = self.try_to_decode(src)
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
        if line.startswith('!!'):
            # force the "standard" behaviour, i.e. first check for the
            # command, then for the variable name to display
            line = line[2:]
            return pdb.Pdb.parseline(self, line)
        # pdb++ "smart command mode": don't execute commands if a variable
        # with the name exits in the current contex; this prevents pdb to quit
        # if you type e.g. 'r[0]' by mystake.
        cmd, arg, newline = pdb.Pdb.parseline(self, line)

        if arg and arg.endswith('?'):
            if hasattr(self, 'do_' + cmd):
                cmd, arg = ('help', cmd)
            elif arg.endswith('??'):
                arg = cmd + arg.split('?')[0]
                cmd = 'source'
                self.do_inspect(arg)
                self.stdout.write('%-28s\n' % Color.set(Color.red, 'Source:'))
            else:
                arg = cmd + arg.split('?')[0]
                cmd = 'inspect'
                return cmd, arg, newline

        if cmd and hasattr(self, 'do_'+cmd) and (cmd in self.curframe.f_globals or
                                                 cmd in self.curframe.f_locals or
                                                 arg.startswith('=')):
            line = '!' + line
            return pdb.Pdb.parseline(self, line)
        return cmd, arg, newline

    def do_inspect(self, arg):
        obj = self._getval(arg)

        data = OrderedDict()
        data['Type'] = type(obj).__name__
        data['String Form'] = str(obj).strip()
        try:
            data['Length'] = len(obj)
        except TypeError:
            pass
        try:
            data['File'] = inspect.getabsfile(obj)
        except TypeError:
            pass

        if (isinstance(obj, type)
                and hasattr(obj, '__init__')
                and getattr(obj, '__module__') != '__builtin__'):
            # Class - show definition and docstring for constructor
            data['Docstring'] = obj.__doc__
            data['Constructor information'] = ''
            try:
                data[' Definition'] = '%s%s' % (arg, signature(obj))
            except ValueError:
                pass
            data[' Docstring'] = obj.__init__.__doc__
        else:
            try:
                data['Definition'] = '%s%s' % (arg, signature(obj))
            except (TypeError, ValueError):
                pass
            data['Docstring'] = obj.__doc__

        for key, value in data.items():
            formatted_key = Color.set(Color.red, key + ':')
            self.stdout.write('%-28s %s\n' % (formatted_key, value))

    def default(self, line):
        self.history.append(line)
        return pdb.Pdb.default(self, line)

    def do_help(self, arg):
        try:
            return pdb.Pdb.do_help(self, arg)
        except AttributeError:
            print("*** No help for '{command}'".format(command=arg),
                  file=self.stdout)
    do_help.__doc__ = pdb.Pdb.do_help.__doc__

    def help_hidden_frames(self):
        print("""\
Some frames might be marked as "hidden": by default, hidden frames are not
shown in the stack trace, and cannot be reached using ``up`` and ``down``.
You can use ``hf_unhide`` to tell pdb to ignore the hidden status (i.e., to
treat hidden frames as normal ones), and ``hf_hide`` to hide them again.
``hf_list`` prints a list of hidden frames.

Frames can marked as hidden in the following ways:

- by using the @pdb.hideframe function decorator

- by having __tracebackhide__=True in the locals or the globals of the function
  (this hides py.test internal stuff)

- by having __unittest=True in the globals of the function (this hides
  unittest internal stuff)
""", file=self.stdout)

    def do_hf_unhide(self, arg):
        """
        {hf_show}
        unhide hidden frames, i.e. make it possible to ``up`` or ``down``
        there
        """
        self.show_hidden_frames = True
        self.refresh_stack()

    def do_hf_hide(self, arg):
        """
        {hf_hide}
        (re)hide hidden frames, if they have been unhidden by ``hf_unhide``
        """
        self.show_hidden_frames = False
        self.refresh_stack()

    def do_hf_list(self, arg):
        for frame_lineno in self.hidden_frames:
            print(self.format_stack_entry(frame_lineno, pdb.line_prefix),
                  file=self.stdout)

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
                try:
                    lines, lineno = inspect.getsourcelines(self.curframe)
                except Exception as e:
                    print('** Error in inspect.getsourcelines: %s **' % e, file=self.stdout)
                    return
        except IOError as e:
            print('** Error: %s **' % e, file=self.stdout)
            return
        if linerange:
            start, end = linerange
            start = max(start, lineno)
            end = min(end, lineno+len(lines))
            lines = lines[start-lineno:end-lineno]
            lineno = start
        self._print_lines_pdbpp(lines, lineno)

    def _print_lines_pdbpp(self, lines, lineno, print_markers=True):
        exc_lineno = self.tb_lineno.get(self.curframe, None)
        lines = [line[:-1] for line in lines] # remove the trailing '\n'
        lines = [line.replace('\t', '    ') for line in lines] # force tabs to 4 spaces
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
            last_marker_line = max(
                self.curframe.f_lineno,
                exc_lineno if exc_lineno else 0) - lineno
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
        print('\n'.join(lines), file=self.stdout)

    do_ll = do_longlist

    def do_list(self, arg):
        oldstdout = self.stdout
        self.stdout = StringIO()
        pdb.Pdb.do_list(self, arg)
        src = self.format_source(self.stdout.getvalue())
        self.stdout = oldstdout
        print(src, file=self.stdout, end='')

    do_list.__doc__ = pdb.Pdb.do_list.__doc__
    do_l = do_list

    def do_continue(self, arg):
        if arg != '':
            self.do_tbreak(arg)
        return pdb.Pdb.do_continue(self, '')
    do_continue.__doc__ = pdb.Pdb.do_continue.__doc__
    do_c = do_cont = do_continue

    def do_pp(self, arg):
        width, height = self.get_terminal_size()
        try:
            pprint.pprint(self._getval(arg), self.stdout, width=width)
        except:
            pass
    do_pp.__doc__ = pdb.Pdb.do_pp.__doc__

    def do_debug(self, arg):
        # this is a hack (as usual :-))
        #
        # inside the original do_debug, there is a call to the global "Pdb" to
        # instantiate the recursive debugger: we want to intercept this call
        # and instantiate *our* Pdb, passing the our custom config. So, we
        # dynamically rebind the globals
        #
        def new_pdb_with_config(*args):
            kwds = dict(Config=self.ConfigFactory)
            return self.__class__(*args, **kwds)
        newglobals = {
            'Pdb': new_pdb_with_config,
            'sys': sys,
            }
        if sys.version_info < (3, ):
            do_debug_func = pdb.Pdb.do_debug.im_func
        else:
            do_debug_func = pdb.Pdb.do_debug

        orig_do_debug = rebind_globals(do_debug_func, newglobals)
        return orig_do_debug(self, arg)
    do_debug.__doc__ = pdb.Pdb.do_debug.__doc__

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
            from rpython.translator.tool.reftracker import track
        except ImportError:
            print('** cannot import pypy.translator.tool.reftracker **', file=self.stdout)
            return
        try:
            val = self._getval(arg)
        except:
            pass
        else:
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
            print('** %s not in the display list **' % arg, file=self.stdout)

    def _print_if_sticky(self):
        if self.sticky:
            self.stdout.write(CLEARSCREEN)
            frame, lineno = self.stack[self.curindex]
            filename = self.canonic(frame.f_code.co_filename)
            s = '> %s(%r)' % (filename, lineno)
            print(s, file=self.stdout)
            print(file=self.stdout)
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
                    print(Color.set(self.config.line_number_color, ' ' + s),
                          file=self.stdout)
                    return
            if '__return__' in frame.f_locals:
                rv = frame.f_locals['__return__']
                try:
                    s = repr(rv)
                except KeyboardInterrupt:
                    raise
                except:
                    s = '(unprintable return value)'
                print(Color.set(self.config.line_number_color, ' return ' + s),
                      file=self.stdout)

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
                print('** Error when parsing argument: %s **' % arg,
                      file=self.stdout)
                return
            self.sticky = True
            self.sticky_ranges[self.curframe] = start, end+1
        else:
            self.sticky = not self.sticky
            self.sticky_range = None
        self._print_if_sticky()

    def print_stack_trace(self):
        try:
            for frame_index, frame_lineno in enumerate(self.stack):
                self.print_stack_entry(frame_lineno, frame_index=frame_index)
        except KeyboardInterrupt:
            pass

    def print_stack_entry(self,
            frame_lineno, prompt_prefix=pdb.line_prefix, frame_index=None):
        frame_index = (frame_index if frame_index is not None else
                       self.curindex)
        frame, lineno = frame_lineno
        if frame is self.curframe:
            print('[%d] >' % frame_index, file=self.stdout, end=' ')
        else:
            print('[%d]  ' % frame_index, file=self.stdout, end=' ')
        print(self.format_stack_entry(frame_lineno,
                                      prompt_prefix),
              file=self.stdout)

    def print_current_stack_entry(self):
        if self.sticky:
            self._print_if_sticky()
        else:
            self.print_stack_entry(self.stack[self.curindex])

    def preloop(self):
        self._print_if_sticky()
        display_list = self._get_display_list()
        for expr, oldvalue in display_list.items():
            newvalue = self._getval_or_undefined(expr)
            # check for identity first; this prevents custom __eq__ to
            # be called at every loop, and also prevents instances
            # whose fields are changed to be displayed
            if newvalue is not oldvalue or newvalue != oldvalue:
                display_list[expr] = newvalue
                print('%s: %r --> %r' % (expr, oldvalue, newvalue),
                      file=self.stdout)


    def _get_position_of_arg(self, arg):
        try:
            obj = self._getval(arg)
        except:
            return None, None, None
        if isinstance(obj, str):
            return obj, 1, None
        try:
            filename = inspect.getabsfile(obj)
            lines, lineno = inspect.getsourcelines(obj)
        except (IOError, TypeError) as e:
            print('** Error: %s **' % e, file=self.stdout)
            return None, None, None
        return filename, lineno, lines

    def do_source(self, arg):
        _, lineno, lines = self._get_position_of_arg(arg)
        if lineno is None:
            return
        self._print_lines_pdbpp(lines, lineno, print_markers=False)

    def do_frame(self, arg):
        try:
            arg = int(arg)
        except (ValueError, TypeError):
            print('*** Expected a number, got "{0}"'.format(arg), file=self.stdout)
            return
        if arg < 0 or arg >= len(self.stack):
            print('*** Out of range', file=self.stdout)
        else:
            self.curindex = arg
            self.curframe = self.stack[self.curindex][0]
            self.curframe_locals = self.curframe.f_locals
            self.print_current_stack_entry()
            self.lineno = None
    do_f = do_frame

    def do_up(self, arg='1'):
        arg = '1' if arg == '' else arg
        try:
            arg = int(arg)
        except (ValueError, TypeError):
            print('*** Expected a number, got "{0}"'.format(arg), file=self.stdout)
            return
        if self.curindex - arg < 0:
            print('*** Oldest frame', file=self.stdout)
        else:
            self.curindex = self.curindex - arg
            self.curframe = self.stack[self.curindex][0]
            self.curframe_locals = self.curframe.f_locals
            self.print_current_stack_entry()
            self.lineno = None
    do_up.__doc__ = pdb.Pdb.do_up.__doc__
    do_u = do_up

    def do_down(self, arg='1'):
        arg = '1' if arg == '' else arg
        try:
            arg = int(arg)
        except (ValueError, TypeError):
            print('*** Expected a number, got "{0}"'.format(arg), file=self.stdout)
            return
        if self.curindex + arg >= len(self.stack):
            print('*** Newest frame', file=self.stdout)
        else:
            self.curindex = self.curindex + arg
            self.curframe = self.stack[self.curindex][0]
            self.curframe_locals = self.curframe.f_locals
            self.print_current_stack_entry()
            self.lineno = None
    do_down.__doc__ = pdb.Pdb.do_down.__doc__
    do_d = do_down

    def get_terminal_size(self):
        try:
            import termios, fcntl, struct
            call = fcntl.ioctl(0, termios.TIOCGWINSZ, "\x00"*8)
            height, width = struct.unpack("hhhh", call)[:2]
        except (SystemExit, KeyboardInterrupt) as e:
            raise
        except:
            width = int(os.environ.get('COLUMNS', 80))
            height = int(os.environ.get('COLUMNS', 24))
        # Work around above returning width, height = 0, 0 in Emacs
        width = width if width != 0 else 80
        height = height if height != 0 else 24
        return width, height

    def _open_editor(self, editor, lineno, filename):
        filename = filename.replace('"', '\\"')
        os.system('%s +%d "%s"' % (editor, lineno, filename))

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

    def _put(self, text):
        stdin_paste = self.config.stdin_paste
        if stdin_paste is None:
            print('** Error: the "stdin_paste" option is not configured **', file=self.stdout)
        filename = self.start_filename
        lineno = self.start_lineno
        self._open_stdin_paste(stdin_paste, lineno, filename, text)

    def do_put(self, arg):
        text = self._get_history_text()
        self._put(text)

    def do_paste(self, arg):
        arg = arg.strip()
        old_stdout = self.stdout
        self.stdout = StringIO()
        self.onecmd(arg)
        text = self.stdout.getvalue()
        self.stdout = old_stdout
        sys.stdout.write(text)
        self._put(text)


# simplified interface

if hasattr(pdb, 'Restart'):
    Restart = pdb.Restart

# copy some functions from pdb.py, but rebind the global dictionary
for name in 'run runeval runctx runcall pm main'.split():
    func = getattr(pdb, name)
    globals()[name] = rebind_globals(func)
del name, func

def post_mortem(t=None, Pdb=Pdb):
    if t is None:
      t = sys.exc_info()[2]
      assert t is not None, "post_mortem outside of exception context"

    p = Pdb()
    p.reset()
    p.interaction(None, t)

GLOBAL_PDB = None

def set_trace(frame=None, Pdb=Pdb, **kwds):
    global GLOBAL_PDB

    if frame is None:
        frame = sys._getframe().f_back

    if GLOBAL_PDB:
        pdb = GLOBAL_PDB
    else:
        filename = frame.f_code.co_filename
        lineno = frame.f_lineno
        pdb = Pdb(start_lineno=lineno, start_filename=filename, **kwds)
        GLOBAL_PDB = pdb

    if hasattr(pdb, 'curframe'):
        del pdb.curframe

    pdb.set_trace(frame)

def cleanup():
    global GLOBAL_PDB

    GLOBAL_PDB = None

# pdb++ specific interface

def xpm(Pdb=Pdb):
    """
    To be used inside an except clause, enter a post-mortem pdb
    related to the just catched exception.
    """
    info = sys.exc_info()
    print(traceback.format_exc())
    post_mortem(info[2], Pdb)


def enable():
    global set_trace
    set_trace = enable.set_trace
enable.set_trace = set_trace

def disable():
    global set_trace
    set_trace = disable.set_trace
disable.set_trace = lambda frame=None, Pdb=Pdb: None

def set_tracex():
    print('PDB!')
set_tracex._dont_inline_ = True

_HIDE_FRAME = object()

def hideframe(func):
    c = get_function_code(func)
    if sys.version_info < (3, ):
        c = types.CodeType(
            c.co_argcount, c.co_nlocals, c.co_stacksize,
            c.co_flags, c.co_code,
            c.co_consts + (_HIDE_FRAME,),
            c.co_names, c.co_varnames, c.co_filename,
            c.co_name, c.co_firstlineno, c.co_lnotab,
            c.co_freevars, c.co_cellvars)
    else:
        # Python 3 takes an additional arg -- kwonlyargcount
        # typically set to 0
        c = types.CodeType(
            c.co_argcount, 0, c.co_nlocals, c.co_stacksize,
            c.co_flags, c.co_code,
            c.co_consts + (_HIDE_FRAME,),
            c.co_names, c.co_varnames, c.co_filename,
            c.co_name, c.co_firstlineno, c.co_lnotab,
            c.co_freevars, c.co_cellvars)
    set_function_code(func, c)
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
        cls.__setattr__ = __setattr__
        return cls
    return decorator

if __name__=='__main__':
    import pdb
    pdb.main()

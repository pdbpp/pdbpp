# -*- coding: utf-8 -*-
"""
pdb++, a drop-in replacement for pdb
====================================

This module extends the stdlib pdb in numerous ways: look at the README for
more details on pdb++ features.
"""

from __future__ import print_function

import sys
import os.path
import inspect
import code
import codecs
import contextlib
import types
import traceback
import subprocess
import threading
import pprint
import re
import signal
from collections import OrderedDict

import fancycompleter
import six
from fancycompleter import Color, Completer, ConfigurableClass

__author__ = 'Antonio Cuni <anto.cuni@gmail.com>'
__url__ = 'http://github.com/antocuni/pdb'
__version__ = fancycompleter.LazyVersion('pdbpp')

try:
    from inspect import signature  # Python >= 3.3
except ImportError:
    try:
        from funcsigs import signature
    except ImportError:
        def signature(obj):
            return ' [pip install funcsigs to show the signature]'

try:
    from functools import lru_cache
except ImportError:
    from functools import wraps

    def lru_cache(maxsize):
        """Simple cache (with no maxsize basically) for py27 compatibility.

        Given that pdb there uses linecache.getline for each line with
        do_list a cache makes a big difference."""

        def dec(fn, *args):
            cache = {}

            @wraps(fn)
            def wrapper(*args):
                key = args
                try:
                    ret = cache[key]
                except KeyError:
                    ret = cache[key] = fn(*args)
                return ret

            return wrapper

        return dec

# If it contains only _, digits, letters, [] or dots, it's probably side
# effects free.
side_effects_free = re.compile(r'^ *[_0-9a-zA-Z\[\].]* *$')

RE_COLOR_ESCAPES = re.compile("(\x1b[^m]+m)+")
RE_REMOVE_FANCYCOMPLETER_ESCAPE_SEQS = re.compile(r"\x1b\[[\d;]+m")

if sys.version_info < (3, ):
    from io import BytesIO as StringIO
else:
    from io import StringIO


local = threading.local()
local.GLOBAL_PDB = None
local._pdbpp_completing = False
local._pdbpp_in_init = False


def __getattr__(name):
    """Backward compatibility (Python 3.7+)"""
    if name == "GLOBAL_PDB":
        return local.GLOBAL_PDB
    raise AttributeError("module '{}' has no attribute '{}'".format(__name__, name))


def import_from_stdlib(name):
    import code  # arbitrary module which stays in the same dir as pdb
    result = types.ModuleType(name)

    stdlibdir, _ = os.path.split(code.__file__)
    pyfile = os.path.join(stdlibdir, name + '.py')
    with open(pyfile) as f:
        src = f.read()
    co_module = compile(src, pyfile, 'exec', dont_inherit=True)
    exec(co_module, result.__dict__)

    return result


pdb = import_from_stdlib('pdb')


def _newfunc(func, newglobals):
    newfunc = types.FunctionType(func.__code__, newglobals, func.__name__,
                                 func.__defaults__, func.__closure__)
    if sys.version_info >= (3, ):
        newfunc.__annotations__ = func.__annotations__
        newfunc.__kwdefaults__ = func.__kwdefaults__
    return newfunc


def rebind_globals(func, newglobals):
    if hasattr(func, "__code__"):
        return _newfunc(func, newglobals)

    import functools

    if isinstance(func, functools.partial):
        return functools.partial(
            _newfunc(func.func, newglobals), *func.args, **func.keywords
        )

    raise ValueError("cannot handle func {!r}".format(func))


class DefaultConfig(object):
    prompt = '(Pdb++) '
    highlight = True
    sticky_by_default = False

    # Pygments.
    use_pygments = None  # Tries to use it if available.
    pygments_formatter_class = None  # Defaults to autodetect, based on $TERM.
    pygments_formatter_kwargs = {}
    # Legacy options.  Should use pygments_formatter_kwargs instead.
    bg = 'dark'
    colorscheme = None

    editor = None  # Autodetected if unset.
    stdin_paste = None       # for emacs, you can use my bin/epaste script
    truncate_long_lines = True
    exec_if_unfocused = None
    disable_pytest_capturing = False
    encodings = ('utf-8', 'latin-1')

    enable_hidden_frames = True
    show_hidden_frames_count = True

    line_number_color = Color.turquoise
    filename_color = Color.yellow
    current_line_color = "39;49;7"  # default fg, bg, inversed

    show_traceback_on_error = True
    show_traceback_on_error_limit = None

    # Default keyword arguments passed to ``Pdb`` constructor.
    default_pdb_kwargs = {
    }

    def setup(self, pdb):
        pass

    def before_interaction_hook(self, pdb):
        pass


def setbgcolor(line, color):
    # hack hack hack
    # add a bgcolor attribute to all escape sequences found
    import re
    setbg = '\x1b[%sm' % color
    regexbg = '\\1;%sm' % color
    result = setbg + re.sub('(\x1b\\[.*?)m', regexbg, line) + '\x1b[00m'
    if os.environ.get('TERM') == 'eterm-color':
        # it seems that emacs' terminal has problems with some ANSI escape
        # sequences. Eg, 'ESC[44m' sets the background color in all terminals
        # I tried, but not in emacs. To set the background color, it needs to
        # have also an explicit foreground color, e.g. 'ESC[37;44m'. These
        # three lines are a hack, they try to add a foreground color to all
        # escape sequences which are not recognized by emacs. However, we need
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


class ArgWithCount(str):
    """Extend arguments with a count, e.g. "10pp …"."""
    def __new__(cls, value, count, **kwargs):
        obj = super(ArgWithCount, cls).__new__(cls, value)
        obj.cmd_count = count
        return obj

    def __repr__(self):
        return "<{} cmd_count={!r} value={}>".format(
            self.__class__.__name__,
            self.cmd_count,
            super(ArgWithCount, self).__repr__(),
        )


class PdbMeta(type):
    def __call__(cls, *args, **kwargs):
        """Reuse an existing instance with ``pdb.set_trace()``."""

        # Prevent recursion errors with pdb.set_trace() during init/debugging.
        if getattr(local, "_pdbpp_in_init", False):
            class OrigPdb(pdb.Pdb, object):
                def set_trace(self, frame=None):
                    print("pdb++: using pdb.Pdb for recursive set_trace.")
                    if frame is None:
                        frame = sys._getframe().f_back
                    super(OrigPdb, self).set_trace(frame)
            orig_pdb = OrigPdb.__new__(OrigPdb)
            # Remove any pdb++ only kwargs.
            kwargs.pop("Config", None)
            orig_pdb.__init__(*args, **kwargs)
            local._pdbpp_in_init = False
            return orig_pdb
        local._pdbpp_in_init = True

        global_pdb = getattr(local, "GLOBAL_PDB", None)
        if global_pdb:
            use_global_pdb = kwargs.pop(
                "use_global_pdb",
                (
                    not global_pdb._in_interaction
                    and os.environ.get("PDBPP_REUSE_GLOBAL_PDB", "1") == "1"
                ),
            )
        else:
            use_global_pdb = kwargs.pop("use_global_pdb", True)

        frame = sys._getframe().f_back
        called_for_set_trace = PdbMeta.called_for_set_trace(frame)
        if (
            use_global_pdb
            and global_pdb
            and called_for_set_trace
            and (
                hasattr(global_pdb, "_force_use_as_global_pdb")
                or cls.use_global_pdb_for_class(global_pdb, cls)
            )
        ):
            if hasattr(global_pdb, "botframe"):
                # Do not stop while tracing is active (in _set_stopinfo).
                # But skip it with instances that have not called set_trace
                # before.
                # Excplicitly unset tracing function always (with breakpoints).
                sys.settrace(None)
                global_pdb.set_continue()
                global_pdb._set_trace_use_next = True

            stdout = kwargs.get("stdout", sys.stdout)
            global_pdb._setup_streams(stdout=stdout)

            local._pdbpp_in_init = False
            return global_pdb

        obj = cls.__new__(cls)
        if called_for_set_trace:
            kwargs.setdefault("start_filename", called_for_set_trace.f_code.co_filename)
            kwargs.setdefault("start_lineno", called_for_set_trace.f_lineno)

        if "set_global_pdb" in kwargs:
            set_global_pdb = kwargs.pop("set_global_pdb", use_global_pdb)
            if set_global_pdb:
                obj._force_use_as_global_pdb = True
        else:
            set_global_pdb = use_global_pdb
        obj.__init__(*args, **kwargs)
        if set_global_pdb:
            obj._env = {"HOME": os.environ.get("HOME")}
            local.GLOBAL_PDB = obj
        local._pdbpp_in_init = False
        return obj

    @classmethod
    def use_global_pdb_for_class(cls, obj, C):
        _env = getattr(obj, "_env", None)
        if _env is not None and _env.get("HOME") != os.environ.get("HOME"):
            return False
        if type(obj) == C:
            return True
        if getattr(obj, "_use_global_pdb_for_class", None) == C:
            return True
        if sys.version_info < (3, 3):
            return inspect.getsourcelines(obj.__class__) == inspect.getsourcelines(C)
        return C.__qualname__ == obj.__class__.__qualname__

    @staticmethod
    def called_for_set_trace(frame):
        called_for_set_trace = False
        while frame:
            if (
                frame.f_code.co_name == "set_trace"
                and frame.f_back
                and "set_trace"
                in (frame.f_back.f_code.co_names + frame.f_back.f_code.co_varnames)
            ):
                called_for_set_trace = frame
                break
            frame = frame.f_back
        return called_for_set_trace


@six.add_metaclass(PdbMeta)
class Pdb(pdb.Pdb, ConfigurableClass, object):

    DefaultConfig = DefaultConfig
    config_filename = '.pdbrc.py'
    disabled = False
    fancycompleter = None

    _in_interaction = False

    def __init__(self, *args, **kwds):
        self.ConfigFactory = kwds.pop('Config', None)
        self.start_lineno = kwds.pop('start_lineno', None)
        self.start_filename = kwds.pop('start_filename', None)

        self.config = self.get_config(self.ConfigFactory)
        self.config.setup(self)

        if "PDBPP_COLORS" in os.environ:
            use_colors = bool(int(os.environ["PDBPP_COLORS"]))
            self.config.highlight = self.config.use_pygments = use_colors

        if self.config.disable_pytest_capturing:
            self._disable_pytest_capture_maybe()
        kwargs = self.config.default_pdb_kwargs.copy()
        kwargs.update(**kwds)
        super(Pdb, self).__init__(*args, **kwargs)
        self.prompt = self.config.prompt
        self.display_list = {}  # frame --> (name --> last seen value)
        self.tb_lineno = {}  # frame --> lineno where the exception raised
        self.history = []
        self.show_hidden_frames = False
        self._hidden_frames = []

        # Sticky mode.
        self.sticky = self.config.sticky_by_default
        self.first_time_sticky = self.sticky
        self.sticky_ranges = {}  # frame --> (start, end)
        self._sticky_messages = []  # Message queue for sticky mode.
        self._sticky_need_cls = False
        self._sticky_skip_cls = False

        self._setup_streams(stdout=self.stdout)

    @property
    def prompt(self):
        return self._prompt

    @prompt.setter
    def prompt(self, value):
        """Ensure there is "++" in the prompt always."""
        if "++" not in value:
            m = re.match(r"^(.*\w)(\s*\W\s*)?$", value)
            if m:
                value = "{}++{}".format(*m.groups(""))
        self._prompt = value

    def _setup_streams(self, stdout):
        self.stdout = self.ensure_file_can_write_unicode(stdout)

    def ensure_file_can_write_unicode(self, f):
        # Wrap with an encoder, but only if not already wrapped
        if (not hasattr(f, 'stream')
                and getattr(f, 'encoding', False)
                and f.encoding.lower() != 'utf-8'):
            f = codecs.getwriter('utf-8')(getattr(f, 'buffer', f))

        return f

    def _disable_pytest_capture_maybe(self):
        try:
            import py.test
            # Force raising of ImportError if pytest is not installed.
            py.test.config
        except (ImportError, AttributeError):
            return
        try:
            capman = py.test.config.pluginmanager.getplugin('capturemanager')
            capman.suspendcapture()
        except KeyError:
            pass
        except AttributeError:
            # Newer pytest with support ready, or very old py.test for which
            # this hack does not work.
            pass

    def _install_linecache_wrapper(self):
        """Disable linecache.checkcache to not invalidate caches.

        This gets installed permanently to also bypass e.g. pytest using
        `inspect.getsource`, which would invalidate it outside of the
        interaction them.
        """
        if not hasattr(self, "_orig_linecache_checkcache"):
            import linecache

            # Save it, although not used really (can be useful for debugging).
            self._orig_linecache_checkcache = linecache.checkcache

            def _linecache_checkcache(*args, **kwargs):
                return

            linecache.checkcache = _linecache_checkcache

    def interaction(self, frame, traceback):
        if frame is None:
            # Skip clearing screen if called with no frame (e.g. via pdb.main).
            self._sticky_skip_cls = True
        self._install_linecache_wrapper()

        self._in_interaction = True
        try:
            return self._interaction(frame, traceback)
        finally:
            self._in_interaction = False

    def _interaction(self, frame, traceback):
        # Restore the previous signal handler at the Pdb prompt.
        if getattr(pdb.Pdb, '_previous_sigint_handler', None):
            try:
                signal.signal(signal.SIGINT, pdb.Pdb._previous_sigint_handler)
            except ValueError:  # ValueError: signal only works in main thread
                pass
            else:
                pdb.Pdb._previous_sigint_handler = None
        ret = self.setup(frame, traceback)
        if ret:
            # no interaction desired at this time (happens if .pdbrc contains
            # a command like "continue")
            self.forget()
            return
        if self.config.exec_if_unfocused:
            self.exec_if_unfocused()

        # Handle post mortem via main: add exception similar to user_exception.
        if frame is None and traceback:
            exc = sys.exc_info()[:2]
            if exc != (None, None):
                self.curframe.f_locals['__exception__'] = exc

        if not self.sticky:
            self.print_stack_entry(self.stack[self.curindex])
            self.print_hidden_frames_count()

        with self._custom_completer():
            self.config.before_interaction_hook(self)
            # Use _cmdloop on py3 which catches KeyboardInterrupt.
            if hasattr(self, '_cmdloop'):
                self._cmdloop()
            else:
                self.cmdloop()

        self.forget()

    def break_here(self, frame):
        ret = super(Pdb, self).break_here(frame)
        if ret:
            # Skip clearing screen if invoked via breakpoint, which e.g.
            # might execute/display output from commands.
            self._sticky_skip_cls = True
        return ret

    def _sticky_handle_cls(self):
        if self._sticky_skip_cls:
            self._sticky_skip_cls = False
            return
        if not self._sticky_need_cls:
            return

        self.stdout.write(CLEARSCREEN)
        self.stdout.flush()
        self._sticky_need_cls = False

    def postcmd(self, stop, line):
        """Handle clearing of the screen for sticky mode."""
        stop = super(Pdb, self).postcmd(stop, line)
        if self.sticky:
            if stop and not self.commands_defining:
                self._sticky_handle_cls()
            else:
                self._flush_sticky_messages()
        return stop

    def _flush_sticky_messages(self):
        if self._sticky_messages:
            for msg in self._sticky_messages:
                print(msg, file=self.stdout)
            self._sticky_messages = []
        self._sticky_last_frame = self.stack[self.curindex]

    def set_continue(self):
        if self.sticky:
            self._sticky_skip_cls = True
        super(Pdb, self).set_continue()

    def set_quit(self):
        if self.sticky:
            self._sticky_skip_cls = True
        super(Pdb, self).set_quit()

    def _setup_fancycompleter(self):
        """Similar to fancycompleter.setup(), but returning the old completer."""
        if not self.fancycompleter:
            self.fancycompleter = Completer(namespace={})
        completer = self.fancycompleter
        readline = completer.config.readline
        old_completer = readline.get_completer()
        if fancycompleter.has_leopard_libedit(completer.config):
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind('tab: complete')
        readline.set_completer(self.complete)
        return old_completer

    @contextlib.contextmanager
    def _custom_completer(self):
        old_completer = self._setup_fancycompleter()

        self._lastcompstate = [None, 0]
        try:
            yield
        finally:
            self.fancycompleter.config.readline.set_completer(old_completer)

    def print_hidden_frames_count(self):
        n = len(self._hidden_frames)
        if n and self.config.show_hidden_frames_count:
            plural = n > 1 and "s" or ""
            print(
                "   %d frame%s hidden (try 'help hidden_frames')" % (n, plural),
                file=self.stdout,
            )

    def exec_if_unfocused(self):
        import os
        import wmctrl
        term = os.getenv('TERM', '')
        try:
            winid = int(os.getenv('WINDOWID'))
        except (TypeError, ValueError):
            return  # cannot find WINDOWID of the terminal
        active_win = wmctrl.Window.get_active()
        if not active_win or (int(active_win.id, 16) != winid) and \
           not (active_win.wm_class == 'emacs.Emacs' and term.startswith('eterm')):
            os.system(self.config.exec_if_unfocused)

    def setup(self, frame, tb):
        ret = super(Pdb, self).setup(frame, tb)
        if not ret:
            while tb:
                lineno = lasti2lineno(tb.tb_frame.f_code, tb.tb_lasti)
                self.tb_lineno[tb.tb_frame] = lineno
                tb = tb.tb_next
        return ret

    def _is_hidden(self, frame):
        if not self.config.enable_hidden_frames:
            return False

        # Decorated code is always considered to be hidden.
        consts = frame.f_code.co_consts
        if consts and consts[-1] is _HIDE_FRAME:
            return True

        # Do not hide if this frame contains the initial set_trace.
        if frame is getattr(self, "_via_set_trace_frame", None):
            return False

        if frame.f_globals.get('__unittest'):
            return True

        # `f_locals` might be a list (checked via PyMapping_Check).
        try:
            tbh = frame.f_locals["__tracebackhide__"]
        except (TypeError, KeyError):
            try:
                tbh = frame.f_globals["__tracebackhide__"]
            except (TypeError, KeyError):
                return False
        return bool(tbh)

    def get_stack(self, f, t):
        # show all the frames, except the ones that explicitly ask to be hidden
        fullstack, idx = super(Pdb, self).get_stack(f, t)
        self.fullstack = fullstack
        return self.compute_stack(fullstack, idx)

    def compute_stack(self, fullstack, idx=None):
        if not fullstack:
            return fullstack, idx if idx is not None else 0
        if idx is None:
            idx = len(fullstack) - 1
        if self.show_hidden_frames:
            return fullstack, idx

        self._hidden_frames = []
        newstack = []
        for frame, lineno in fullstack:
            if self._is_hidden(frame):
                self._hidden_frames.append((frame, lineno))
            else:
                newstack.append((frame, lineno))
        if not newstack:
            newstack.append(self._hidden_frames.pop())
        newidx = idx - len(self._hidden_frames)
        return newstack, newidx

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

    def reset(self):
        """Set values of attributes as ready to start debugging.

        This overrides Bdb.reset to not clear the linecache (bpo-39967)."""
        self.botframe = None
        self._set_stopinfo(None, None)
        self.forget()

    def forget(self):
        if not getattr(local, "_pdbpp_completing", False):
            super(Pdb, self).forget()

    @classmethod
    def _get_all_completions(cls, complete, text):
        r = []
        i = 0
        while True:
            comp = complete(text, i)
            if comp is None:
                break
            i += 1
            r.append(comp)
        return r

    @contextlib.contextmanager
    def _patch_readline_for_pyrepl(self):
        """Patch readline module used in original Pdb.complete."""
        uses_pyrepl = self.fancycompleter.config.readline != sys.modules["readline"]

        if not uses_pyrepl:
            yield
            return

        # Make pdb.Pdb.complete use pyrepl's readline.
        orig_readline = sys.modules["readline"]
        sys.modules["readline"] = self.fancycompleter.config.readline
        try:
            yield
        finally:
            sys.modules["readline"] = orig_readline

    def complete(self, text, state):
        try:
            return self._complete(text, state)
        except Exception as exc:
            self.error("error during completion: {}".format(exc))
            if self.config.show_traceback_on_error:
                __import__("traceback").print_exc(file=self.stdout)

    def _complete(self, text, state):
        """Handle completions from fancycompleter and original pdb."""
        if state == 0:
            local._pdbpp_completing = True

            self._completions = []

            # Get completions from fancycompleter.
            mydict = self.curframe.f_globals.copy()
            mydict.update(self.curframe_locals)
            completer = Completer(mydict)
            completions = self._get_all_completions(completer.complete, text)

            if self.fancycompleter.config.use_colors:
                clean_fancy_completions = set([
                    RE_REMOVE_FANCYCOMPLETER_ESCAPE_SEQS.sub("", x)
                    for x in completions
                ])
            else:
                clean_fancy_completions = completions

            # Get completions from original pdb.
            pdb_completions = []
            with self._patch_readline_for_pyrepl():
                real_pdb = super(Pdb, self)
                for x in self._get_all_completions(real_pdb.complete, text):
                    if x not in clean_fancy_completions:
                        pdb_completions.append(x)

            # Ignore "\t" as only completion from fancycompleter, if there are
            # pdb completions.
            if completions == ["\t"] and pdb_completions:
                completions = []

            self._completions = completions
            if pdb_completions:
                pdb_prefix = fancycompleter.commonprefix(pdb_completions)
                if '.' in text and pdb_prefix and len(pdb_completions) > 1:
                    # Remove prefix for attr_matches from pdb completions.
                    dotted = text.split('.')
                    prefix = '.'.join(dotted[:-1]) + '.'
                    prefix_len = len(prefix)
                    pdb_completions = [
                        x[prefix_len:] if x.startswith(prefix) else x
                        for x in pdb_completions
                    ]
                if len(completions) == 1 and "." in completions[0]:
                    if pdb_prefix:
                        pdb_completions = []

                for x in pdb_completions:
                    if x not in clean_fancy_completions:
                        self._completions.append(x)
            else:
                self._completions = completions

            self._filter_completions(text)

            local._pdbpp_completing = False

        try:
            return self._completions[state]
        except IndexError:
            return None

    def _filter_completions(self, text):
        # Remove anything prefixed with "_" / "__" by default, but only
        # display it on additional request (3rd tab, after pyrepl's "[not
        # unique]"), or if the prefix is used already.
        if text == self._lastcompstate[0]:
            if self._lastcompstate[1] > 0:
                return
            self._lastcompstate[1] += 1
        else:
            self._lastcompstate[0] = text
            self._lastcompstate[1] = 0

        if text[-1:] != "_":
            self._completions = [
                x
                for x in self._completions
                if RE_COLOR_ESCAPES.sub("", x)[:1] != "_"
            ]
        elif text[-2:] != "__":
            self._completions = [
                x
                for x in self._completions
                if RE_COLOR_ESCAPES.sub("", x)[:2] != "__"
            ]

    stack_entry_regexp = re.compile(r'(.*?)\(([0-9]+?)\)(.*)', re.DOTALL)

    def format_stack_entry(self, frame_lineno, lprefix=': '):
        entry = super(Pdb, self).format_stack_entry(frame_lineno, lprefix)
        entry = self.try_to_decode(entry)
        if self.config.highlight:
            match = self.stack_entry_regexp.match(entry)
            if match:
                filename, lineno, other = match.groups()
                filename = Color.set(self.config.filename_color, filename)
                lineno = Color.set(self.config.line_number_color, lineno)
                entry = '%s(%s)%s' % (filename, lineno, other)
        if self.config.use_pygments is not False:
            loc, _, source = entry.rpartition(lprefix)
            if _:
                entry = loc + _ + self.format_source(source).rstrip()
        return entry

    def try_to_decode(self, s):
        for encoding in self.config.encodings:
            try:
                return s.decode(encoding)
            except (UnicodeDecodeError, AttributeError):
                pass
        return s

    def try_to_encode(self, s):
        for encoding in self.config.encodings:
            try:
                return s.encode(encoding)
            except (UnicodeDecodeError, AttributeError):
                pass
        return s

    def _get_source_highlight_function(self):
        try:
            import pygments
            import pygments.lexers
        except ImportError:
            return False

        try:
            pygments_formatter = self._get_pygments_formatter()
        except Exception as exc:
            self.message("pdb++: could not setup Pygments, disabling: {}".format(
                exc
            ))
            return False

        lexer = pygments.lexers.PythonLexer(stripnl=False)

        def syntax_highlight(src):
            return pygments.highlight(src, lexer, pygments_formatter)

        return syntax_highlight

    def _get_pygments_formatter(self):
        if hasattr(self.config, 'formatter'):
            # Deprecated, never documented.
            # Not optimal, since it involves creating the formatter in
            # the config already, although it might never be used.
            return self.config.formatter

        if self.config.pygments_formatter_class:
            from importlib import import_module

            def import_string(dotted_path):
                module_path, class_name = dotted_path.rsplit('.', 1)
                module = import_module(module_path)
                return getattr(module, class_name)

            Formatter = import_string(self.config.pygments_formatter_class)
        else:
            import pygments.formatters

            if getattr(self.config, "use_terminal256formatter", None) is not None:
                # Deprecated, never really documented (only changelog).
                if self.config.use_terminal256formatter:
                    Formatter = pygments.formatters.Terminal256Formatter
                else:
                    Formatter = pygments.formatters.TerminalFormatter
            else:
                term = os.environ.get("TERM", "")
                if term in ("xterm-kitty",):
                    Formatter = pygments.formatters.TerminalTrueColorFormatter
                elif "256color" in term:
                    Formatter = pygments.formatters.Terminal256Formatter
                else:
                    Formatter = pygments.formatters.TerminalFormatter

        formatter_kwargs = {
            # Only used by TerminalFormatter.
            "bg": self.config.bg,
            "colorscheme": self.config.colorscheme,
            "style": "default",
        }
        formatter_kwargs.update(self.config.pygments_formatter_kwargs)

        return Formatter(**formatter_kwargs)

    def format_source(self, src):
        if self.config.use_pygments is False:
            return src

        if not hasattr(self, "_highlight"):
            self._highlight = self._get_source_highlight_function()

            if self._highlight is False:
                if self.config.use_pygments is True:
                    self.message("Could not import pygments, disabling.")
                self.config.use_pygments = False
                return src

        return self._highlight_cached(src)

    @lru_cache(maxsize=64)
    def _highlight_cached(self, src):
        src = self.try_to_decode(src)
        return self._highlight(src)

    def _format_line(self, lineno, marker, line, lineno_width):
        lineno = ('%%%dd' % lineno_width) % lineno
        if self.config.highlight:
            lineno = Color.set(self.config.line_number_color, lineno)
        line = '%s  %2s %s' % (lineno, marker, line)
        return line

    def execRcLines(self):
        self._pdbpp_executing_rc_lines = True
        try:
            return super(Pdb, self).execRcLines()
        finally:
            del self._pdbpp_executing_rc_lines

    def parseline(self, line):
        if getattr(self, "_pdbpp_executing_rc_lines", False):
            return super(Pdb, self).parseline(line)

        if line.startswith('!!'):
            # Force the "standard" behaviour, i.e. first check for the
            # command, then for the variable name to display.
            line = line[2:]
            cmd, arg, newline = super(Pdb, self).parseline(line)
            return cmd, arg, "!!" + newline

        if line.endswith('?') and not line.startswith("!"):
            arg = line.split('?', 1)[0]
            if line.endswith('??'):
                cmd = "inspect_with_source"
            elif arg == '' or (
                hasattr(self, 'do_' + arg)
                and arg not in self.curframe.f_globals
                and arg not in self.curframe_locals
            ):
                cmd = "help"
            else:
                cmd = "inspect"
            return cmd, arg, line

        # pdb++ "smart command mode": don't execute commands if a variable
        # with the name exists in the current context;
        # This prevents pdb to quit if you type e.g. 'r[0]' by mistake.
        cmd, arg, newline = super(Pdb, self).parseline(line)

        if cmd:
            # prefixed strings.
            if (
                cmd in ("b", "f", "r", "u")
                and len(newline) > 1
                and (newline[1] == "'" or newline[1] == '"')
            ):
                cmd, arg, newline = None, None, line
            else:
                # Handle "count" prefix with commands, transferring it to "arg".
                m = re.match(r"(\d+)(\w+)", cmd)
                if m:
                    arg = ArgWithCount(arg, count=int(m.group(1)))
                    cmd = m.group(2)

                if hasattr(self, "do_" + cmd):
                    if (
                        self.curframe
                        and (
                            cmd in self.curframe.f_globals
                            or cmd in self.curframe_locals
                        )
                        and cmd + arg == line  # not for "debug ..." etc
                    ) or arg.startswith("="):
                        cmd, arg, newline = None, None, line
                    elif arg.startswith("(") and cmd in ("list", "next"):
                        # heuristic: handle "list(...", "next(..." etc as builtin.
                        cmd, arg, newline = None, None, line

        # Fix cmd to not be None when used in completions.
        # This would trigger a TypeError (instead of AttributeError) in
        # Cmd.complete (https://bugs.python.org/issue35270).
        if cmd is None:
            f = sys._getframe()
            while f.f_back:
                f = f.f_back
                if f.f_code.co_name == "complete":
                    cmd = ""
                    break

        return cmd, arg, newline

    def do_inspect(self, arg):
        """Inspect argument.  Used with `obj?`."""
        self._do_inspect(arg, with_source=False)

    def do_inspect_with_source(self, arg):
        """Inspect argument with source (if available).  Used with `obj??`."""
        self._do_inspect(arg, with_source=True)

    def _do_inspect(self, arg, with_source=False):
        try:
            obj = self._getval(arg)
        except Exception:
            return

        data = OrderedDict()
        data['Type'] = type(obj).__name__
        data['String Form'] = str(obj).strip()
        try:
            data['Length'] = str(len(obj))
        except TypeError:
            pass
        try:
            data['File'] = inspect.getabsfile(obj)
        except TypeError:
            pass
        else:
            try:
                data['File'] += ":" + str(obj.__code__.co_firstlineno)
            except AttributeError:
                pass

        if (isinstance(obj, type)
                and hasattr(obj, '__init__')
                and getattr(obj, '__module__') != '__builtin__'):
            # Class - show definition and docstring for constructor
            data['Docstring'] = inspect.getdoc(obj)
            data['Constructor information'] = ''
            try:
                data['  Definition'] = '%s%s' % (arg, signature(obj))
            except ValueError:
                pass
            data['  Docstring'] = inspect.getdoc(obj.__init__)
        else:
            try:
                data['Definition'] = '%s%s' % (arg, signature(obj))
            except (TypeError, ValueError):
                pass
            data['Docstring'] = inspect.getdoc(obj)

        for key, value in data.items():
            formatted_key = Color.set(Color.red, key + ':')
            if value is None:
                continue
            if value:
                first_line, _, lines = str(value).partition("\n")
                formatted_value = first_line
                if lines:
                    indent = " " * 16
                    formatted_value += "\n" + "\n".join(
                        indent + line
                        for line in lines.splitlines()
                    )
            else:
                formatted_value = ""
            self.stdout.write('%-28s %s\n' % (formatted_key, formatted_value))

        if with_source:
            self.stdout.write("%-28s" % Color.set(Color.red, "Source:"))
            _, lineno, lines = self._get_position_of_obj(obj, quiet=True)
            if lines is None:
                self.stdout.write(" -\n")
            else:
                self.stdout.write("\n")
                self._print_lines_pdbpp(lines, lineno, print_markers=False)

    def default(self, line):
        """Patched version to fix namespace with list comprehensions.

        Fixes https://bugs.python.org/issue21161.
        """
        self.history.append(line)
        if line[:1] == '!':
            line = line[1:]
        locals = self.curframe_locals
        ns = self.curframe.f_globals.copy()
        ns.update(locals)
        try:
            code = compile(line + '\n', '<stdin>', 'single')
            save_stdout = sys.stdout
            save_stdin = sys.stdin
            save_displayhook = sys.displayhook
            try:
                sys.stdin = self.stdin
                sys.stdout = self.stdout
                sys.displayhook = self.displayhook
                exec(code, ns, locals)
            finally:
                sys.stdout = save_stdout
                sys.stdin = save_stdin
                sys.displayhook = save_displayhook
        except:
            exc_info = sys.exc_info()[:2]
            self.error(traceback.format_exception_only(*exc_info)[-1].strip())

    def do_help(self, arg):
        try:
            return super(Pdb, self).do_help(arg)
        except AttributeError:
            print("*** No help for '{command}'".format(command=arg),
                  file=self.stdout)
    do_help.__doc__ = pdb.Pdb.do_help.__doc__

    def help_hidden_frames(self):
        print("""\
Some frames might be marked as "hidden": by default, hidden frames are not
shown in the stack trace, and cannot be reached using ``up`` and ``down``.
You can use ``hf_unhide`` to tell pdb++ to ignore the hidden status (i.e., to
treat hidden frames as normal ones), and ``hf_hide`` to hide them again.
``hf_list`` prints a list of hidden frames.

Frames can be marked as hidden in the following ways:

- by using the ``@pdb.hideframe`` function decorator

- by having ``__tracebackhide__=True`` in the locals or the globals of the
  function (this is used by pytest)

- by having ``__unittest=True`` in the globals of the function (this hides
  unittest internal stuff)

- by providing a list of skip patterns to the Pdb class constructor.  This
  list defaults to ``skip=["importlib._bootstrap"]``.

Note that the initial frame where ``set_trace`` was called from is not hidden,
except for when using the function decorator.
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
        for frame_lineno in self._hidden_frames:
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
        self._printlonglist(max_lines=False)

    do_ll = do_longlist

    def _printlonglist(self, linerange=None, max_lines=None):
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
                    print('** Error in inspect.getsourcelines: %s **' %
                          e, file=self.stdout)
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
        self._print_lines_pdbpp(lines, lineno, max_lines=max_lines)

    @staticmethod
    def _truncate_to_visible_length(s, maxlength):
        """Truncate string to visible length (with escape sequences ignored)."""
        matches = list(RE_COLOR_ESCAPES.finditer(s))
        if not matches:
            return s[:maxlength]

        ret = ""
        total_visible_len = 0
        pos = 0
        for m in matches:
            m_start = m.regs[0][0]
            m_end = m.regs[0][1]
            add_visible = s[pos:m_start]
            len_visible = m_start - pos
            overflow = (len_visible + total_visible_len) - maxlength
            if overflow >= 0:
                if overflow == 0:
                    ret += add_visible
                else:
                    ret += add_visible[:-overflow]
                ret += s[m_start:m_end]
                break
            total_visible_len += len_visible
            ret += add_visible
            ret += s[m_start:m_end]
            pos = m_end
        else:
            assert maxlength - total_visible_len > 0
            rest = s[m_end:]
            ret += rest[:maxlength - total_visible_len]

        # Keep reset sequence (in last match).
        if len(ret) != len(s):
            last_m_start, last_m_end = matches[-1].span()
            if last_m_end == len(s):
                reset_seq = s[last_m_start:last_m_end]
                if not ret.endswith(reset_seq):
                    ret += reset_seq

        assert len(RE_COLOR_ESCAPES.sub("", ret)) <= maxlength
        return ret

    def _cut_lines(self, lines, lineno, max_lines):
        max_lines = max(6, len(lines) if not max_lines else max_lines)
        if len(lines) <= max_lines:
            for i, line in enumerate(lines, lineno):
                yield i, line
            return

        cutoff = len(lines) - max_lines

        # Keep certain top lines.
        # Keeps decorators, but not functions, which are displayed at the top
        # already (stack information).
        # TODO: check behavior with lambdas.
        COLOR_OR_SPACE = r'(?:\x1b[^m]+m|\s)'
        keep_pat = re.compile(
            r'(?:^{col}*@)'
            r'|(?<!\w)lambda(?::|{col})'.format(col=COLOR_OR_SPACE)
        )
        keep_head = 0
        while keep_pat.match(lines[keep_head]):
            keep_head += 1

        if keep_head > 3:
            yield lineno, lines[0]
            yield None, '...'
            yield lineno + keep_head, lines[keep_head-1]
            cutoff -= keep_head - 3
        else:
            for i, line in enumerate(lines[:keep_head]):
                yield lineno + i, line

        exc_lineno = self.tb_lineno.get(self.curframe, None)
        last_marker_line = max(
            self.curframe.f_lineno,
            exc_lineno if exc_lineno else 0) - lineno

        # Place marker / current line in first third of available lines.
        cut_before = min(
            cutoff, max(0, last_marker_line - max_lines + max_lines // 3 * 2)
        )
        cut_after = cutoff - cut_before

        # Adjust for '...' lines.
        cut_after = cut_after + 1 if cut_after > 0 else 0
        if cut_before:
            # Adjust for '...' line.
            cut_before += 1

        for i, line in enumerate(lines[keep_head:], keep_head):
            if cut_before:
                cut_before -= 1
                if cut_before == 0:
                    yield None, '...'
                else:
                    assert cut_before > 0, cut_before
                continue
            elif cut_after and i >= len(lines) - cut_after:
                yield None, '...'
                break
            yield lineno + i, line

    def _print_lines_pdbpp(self, lines, lineno, print_markers=True, max_lines=None):
        lines = [line[:-1] for line in lines]  # remove the trailing '\n'
        lines = [line.replace('\t', '    ')
                 for line in lines]  # force tabs to 4 spaces
        width, height = self.get_terminal_size()

        if self.config.use_pygments is not False:
            src = self.format_source('\n'.join(lines))
            lines = src.splitlines()

        if self.config.truncate_long_lines:
            maxlength = max(width - 9, 16)
            lines = [self._truncate_to_visible_length(line, maxlength)
                     for line in lines]

        lineno_width = len(str(lineno + len(lines)))
        exc_lineno = self.tb_lineno.get(self.curframe, None)

        new_lines = []
        if print_markers:
            set_bg = self.config.highlight and self.config.current_line_color
            for lineno, line in self._cut_lines(lines, lineno, max_lines):
                if lineno is None:
                    new_lines.append(line)
                    continue

                if lineno == self.curframe.f_lineno:
                    marker = '->'
                elif lineno == exc_lineno:
                    marker = '>>'
                else:
                    marker = ''
                line = self._format_line(lineno, marker, line, lineno_width)

                if marker == "->" and set_bg:
                    len_visible = len(RE_COLOR_ESCAPES.sub("", line))
                    line = line + " " * (width - len_visible)
                    line = setbgcolor(line, self.config.current_line_color)
                new_lines.append(line)
        else:
            for i, line in enumerate(lines):
                new_lines.append(self._format_line(lineno, '', line, lineno_width))
                lineno += 1
        print('\n'.join(new_lines), file=self.stdout)

    def _format_color_prefixes(self, lines):
        if not lines:
            return lines

        if self.config.use_pygments is False and not self.config.highlight:
            return lines

        # Format source without prefixes added by pdb, including line numbers.
        prefixes = []
        src_lines = []

        for x in lines:
            prefix, _, src = x.partition('\t')
            prefixes.append(prefix)
            src_lines.append(src)

        RE_LNUM_PREFIX = re.compile(r"^\d+")
        if self.config.highlight:
            prefixes = [
                RE_LNUM_PREFIX.sub(
                    lambda m: Color.set(self.config.line_number_color, m.group(0)),
                    prefix
                )
                for prefix in prefixes
            ]

        return [
            "%s\t%s" % (prefix, src)
            for (prefix, src) in zip(prefixes, src_lines)
        ]

    @contextlib.contextmanager
    def _patch_linecache_for_source_highlight(self):
        orig = pdb.linecache.getlines

        def wrapped_getlines(filename, globals):
            """Wrap linecache.getlines to highlight source (for do_list)."""
            lines = orig(filename, globals)
            source = self.format_source("".join(lines))

            if sys.version_info < (3,):
                source = self.try_to_encode(source)

            return source.splitlines(True)

        pdb.linecache.getlines = wrapped_getlines

        try:
            yield
        finally:
            pdb.linecache.getlines = orig

    def do_list(self, arg):
        """Enhance original do_list with highlighting."""
        if not (self.config.use_pygments is not False or self.config.highlight):
            return super(Pdb, self).do_list(arg)

        with self._patch_linecache_for_source_highlight():

            oldstdout = self.stdout
            self.stdout = StringIO()
            ret = super(Pdb, self).do_list(arg)
            orig_pdb_lines = self.stdout.getvalue().splitlines()
            self.stdout = oldstdout

        for line in self._format_color_prefixes(orig_pdb_lines):
            print(line, file=self.stdout)
        return ret

    do_list.__doc__ = pdb.Pdb.do_list.__doc__
    do_l = do_list

    def _select_frame(self, number):
        """Same as pdb.Pdb, but uses print_current_stack_entry (for sticky)."""
        assert 0 <= number < len(self.stack), (number, len(self.stack))
        self.curindex = number
        self.curframe = self.stack[self.curindex][0]
        self.curframe_locals = self.curframe.f_locals
        self.print_current_stack_entry()
        self.lineno = None

    def do_continue(self, arg):
        if arg != '':
            self._seen_error = False
            self.do_tbreak(arg)
            if self._seen_error:
                return 0
        return super(Pdb, self).do_continue('')
    do_continue.__doc__ = pdb.Pdb.do_continue.__doc__
    do_c = do_cont = do_continue

    def do_p(self, arg):
        """p expression
        Print the value of the expression.
        """
        try:
            val = self._getval(arg)
        except:
            return
        try:
            self.message(repr(val))
        except:
            exc_info = sys.exc_info()[:2]
            self.error(traceback.format_exception_only(*exc_info)[-1].strip())

    def do_pp(self, arg):
        """[width]pp expression
        Pretty-print the value of the expression.
        """
        width = getattr(arg, "cmd_count", None)
        try:
            val = self._getval(arg)
        except:
            return
        if width is None:
            try:
                width, _ = self.get_terminal_size()
            except Exception as exc:
                self.message("warning: could not get terminal size ({})".format(exc))
                width = None
        try:
            pprint.pprint(val, self.stdout, width=width)
        except:
            exc_info = sys.exc_info()[:2]
            self.error(traceback.format_exception_only(*exc_info)[-1].strip())
    do_pp.__doc__ = pdb.Pdb.do_pp.__doc__

    def do_debug(self, arg):
        """debug code
        Enter a recursive debugger that steps through the code
        argument (which is an arbitrary expression or statement to be
        executed in the current environment).
        """
        orig_trace = sys.gettrace()
        if orig_trace:
            sys.settrace(None)
        globals = self.curframe.f_globals
        locals = self.curframe_locals
        Config = self.ConfigFactory

        class PdbppWithConfig(self.__class__):
            def __init__(self_withcfg, *args, **kwargs):
                kwargs.setdefault("Config", Config)
                super(PdbppWithConfig, self_withcfg).__init__(*args, **kwargs)

                # Backport of fix for bpo-31078 (not yet merged).
                self_withcfg.use_rawinput = self.use_rawinput

                local.GLOBAL_PDB = self_withcfg
                local.GLOBAL_PDB._use_global_pdb_for_class = self.__class__

        prev_pdb = local.GLOBAL_PDB
        p = PdbppWithConfig(self.completekey, self.stdin, self.stdout)
        p._prompt = "({}) ".format(self._prompt.strip())
        self.message("ENTERING RECURSIVE DEBUGGER")
        self._flush_sticky_messages()
        try:
            with self._custom_completer():
                sys.call_tracing(p.run, (arg, globals, locals))
        except Exception:
            exc_info = sys.exc_info()[:2]
            self.error(traceback.format_exception_only(*exc_info)[-1].strip())
        finally:
            local.GLOBAL_PDB = prev_pdb
        self.message("LEAVING RECURSIVE DEBUGGER")

        if orig_trace:
            sys.settrace(orig_trace)
        self.lastcmd = p.lastcmd

    do_debug.__doc__ = pdb.Pdb.do_debug.__doc__

    def do_interact(self, arg):
        """
        interact

        Start an interactive interpreter whose global namespace
        contains all the names found in the current scope.
        """
        ns = self.curframe.f_globals.copy()
        ns.update(self.curframe_locals)
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
            print('** cannot import pypy.translator.tool.reftracker **',
                  file=self.stdout)
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
                        self.curframe_locals)
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
        if self.sticky and not self.commands_defining:
            self._sticky_handle_cls()
            width, height = self.get_terminal_size()

            frame, lineno = self.stack[self.curindex]
            stack_entry = self._get_formatted_stack_entry(
                self.stack[self.curindex], "__CUTOFF_MARKER__"
            )
            s = stack_entry.split("__CUTOFF_MARKER__")[0]  # hack
            top_lines = []
            if self._sticky_messages:
                for msg in self._sticky_messages:
                    if msg == "--Return--" and (
                        "__return__" in frame.f_locals
                        or "__exception__" in frame.f_locals
                    ):
                        # Handled below.
                        continue
                    if msg.startswith("--") and msg.endswith("--"):
                        s += ", {}".format(msg)
                    else:
                        top_lines.append(msg)
                self._sticky_messages = []

            if self.config.show_hidden_frames_count:
                n = len(self._hidden_frames)
                if n:
                    plural = n > 1 and "s" or ""
                    s += ", %d frame%s hidden" % (n, plural)
            top_lines.append(s)

            sticky_range = self.sticky_ranges.get(self.curframe, None)

            after_lines = []
            if '__exception__' in frame.f_locals:
                s = self._format_exc_for_sticky(frame.f_locals['__exception__'])
                if s:
                    after_lines.append(s)

            elif getattr(sys, "last_value", None):
                s = self._format_exc_for_sticky((type(sys.last_value), sys.last_value))
                if s:
                    after_lines.append(s)

            elif '__return__' in frame.f_locals:
                rv = frame.f_locals['__return__']
                try:
                    s = repr(rv)
                except KeyboardInterrupt:
                    raise
                except:
                    s = '(unprintable return value)'

                s = ' return ' + s
                if self.config.highlight:
                    s = Color.set(self.config.line_number_color, s)
                after_lines.append(s)

            top_extra_lines = 0
            for line in top_lines:
                print(line, file=self.stdout)
                len_visible = len(RE_COLOR_ESCAPES.sub("", line))
                top_extra_lines += (len_visible - 1) // width + 2
            print(file=self.stdout)

            # Arrange for prompt and extra lines on top (location + newline
            # typically), and keep an empty line at the end (after prompt), so
            # that any output shows up at the top.
            max_lines = height - top_extra_lines - len(after_lines) - 2

            self._printlonglist(sticky_range, max_lines=max_lines)

            for line in after_lines:
                print(line, file=self.stdout)
            self._sticky_need_cls = True

    def _format_exc_for_sticky(self, exc):
        if len(exc) != 2:
            return "pdbpp: got unexpected __exception__: %r" % (exc,)

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
        except Exception as exc:
            try:
                s += '(unprintable exception: %r)' % (exc,)
            except:
                s += '(unprintable exception)'
        else:
            # Use first line only, limited to terminal width.
            s = s.replace("\r", r"\r").replace("\n", r"\n")
            width, _ = self.get_terminal_size()
            if len(s) > width:
                s = s[:width - 1] + "…"

        if self.config.highlight:
            s = Color.set(self.config.line_number_color, s)

        return s

    def do_sticky(self, arg):
        """
        sticky [start end]

        Toggle sticky mode. When in sticky mode, it clear the screen
        and longlist the current functions, making the source
        appearing always in the same position. Useful to follow the
        flow control of a function when doing step-by-step execution.

        If ``start`` and ``end`` are given, sticky mode is enabled and
        only lines within that range (extremes included) will be
        displayed (for the current frame).
        """
        was_sticky = self.sticky
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
        if not was_sticky and self.sticky:
            self._sticky_need_cls = True
        self._print_if_sticky()

    def print_stack_trace(self):
        try:
            for frame_index, frame_lineno in enumerate(self.stack):
                self.print_stack_entry(frame_lineno, frame_index=frame_index)
        except KeyboardInterrupt:
            pass

    def print_stack_entry(
        self, frame_lineno, prompt_prefix=pdb.line_prefix, frame_index=None
    ):
        if self.sticky:
            # Skip display of current frame when sticky mode display it later.
            if sys._getframe(1).f_code.co_name == "bp_commands":
                return
        print(
            self._get_formatted_stack_entry(frame_lineno, prompt_prefix, frame_index),
            file=self.stdout,
        )

    def _get_formatted_stack_entry(
        self, frame_lineno, prompt_prefix=pdb.line_prefix, frame_index=None
    ):
        frame, lineno = frame_lineno
        if frame is self.curframe:
            marker = "> "
        else:
            marker = "  "

        frame_prefix_width = len(str(len(self.stack)))
        if frame_index is None:
            frame_index = self.curindex
            fmt = "{frame_prefix}{marker}"
            lprefix = prompt_prefix  # "\n ->" by default
        else:
            # via/for stack trace
            fmt = "{marker}{frame_prefix}"
            lprefix = "\n     " + (" " * frame_prefix_width)

        # Format stack index (keeping same width across stack).
        frame_prefix = ("[%%%dd] " % frame_prefix_width) % frame_index
        marker_frameno = fmt.format(marker=marker, frame_prefix=frame_prefix)

        return marker_frameno + self.format_stack_entry(frame_lineno, lprefix)

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

    def _get_position_of_arg(self, arg, quiet=False):
        try:
            obj = eval(arg, self.curframe.f_globals, self.curframe_locals)
        except:
            if not quiet:
                exc_info = sys.exc_info()[:2]
                error = traceback.format_exception_only(*exc_info)[-1].strip()
                self.error("failed to eval: {}".format(error))
            return None, None, None
        try:
            return self._get_position_of_obj(obj, quiet=quiet)
        except:
            return None, None, None

    def _get_fnamelineno_for_arg(self, arg):
        filename, lineno, _ = self._get_position_of_arg(arg, quiet=True)
        if filename is None:
            if os.path.exists(arg):
                filename = arg
                lineno = 1
            else:
                m = re.match(r"^(.*):(\d+)$", arg)
                if m:
                    filename, lineno = m.group(1), int(m.group(2))
                else:
                    filename, lineno = arg, 1
                if not os.path.exists(filename):
                    # Like "do_break" does it.
                    filename = self.lookupmodule(filename)
                    if filename is None:
                        lineno = None
                    elif not os.path.exists(filename):
                        filename, lineno = None, None
        return filename, lineno

    def _get_position_of_obj(self, obj, quiet=False):
        if hasattr(inspect, "unwrap"):
            obj = inspect.unwrap(obj)
        if isinstance(obj, str):
            return obj, 1, None
        try:
            filename = inspect.getabsfile(obj)
            lines, lineno = inspect.getsourcelines(obj)
        except (IOError, TypeError) as e:
            if not quiet:
                self.error('could not get obj: {}'.format(e))
            return None, None, None
        return filename, lineno, lines

    def do_source(self, arg):
        _, lineno, lines = self._get_position_of_arg(arg)
        if lineno is None:
            return
        self._print_lines_pdbpp(lines, lineno, print_markers=False)

    def do_frame(self, arg):
        """f(rame) [index]
        Go to given frame.  The first frame is 0, negative index is counted
        from the end (i.e. -1 is the last one).
        Without argument, display current frame.
        """
        if not arg:
            # Just display the frame, without handling sticky.
            self.print_stack_entry(self.stack[self.curindex])
            return

        try:
            arg = int(arg)
        except (ValueError, TypeError):
            print(
                '*** Expected a number, got "{0}"'.format(arg), file=self.stdout)
            return
        if abs(arg) >= len(self.stack):
            print('*** Out of range', file=self.stdout)
            return
        if arg >= 0:
            self.curindex = arg
        else:
            self.curindex = len(self.stack) + arg
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
            print(
                '*** Expected a number, got "{0}"'.format(arg), file=self.stdout)
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
            print(
                '*** Expected a number, got "{0}"'.format(arg), file=self.stdout)
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

    def do_top(self, arg):
        """Go to top (oldest) frame."""
        if self.curindex == 0:
            self.error('Oldest frame')
            return
        self._select_frame(0)
    do_top = do_top

    def do_bottom(self, arg):
        """Go to bottom (newest) frame."""
        if self.curindex + 1 == len(self.stack):
            self.error('Newest frame')
            return
        self._select_frame(len(self.stack) - 1)
    do_bottom = do_bottom

    @staticmethod
    def get_terminal_size():
        fallback = (80, 24)
        try:
            from shutil import get_terminal_size
        except ImportError:
            try:
                import termios
                import fcntl
                import struct
                call = fcntl.ioctl(0, termios.TIOCGWINSZ, "\x00"*8)
                height, width = struct.unpack("hhhh", call)[:2]
            except (SystemExit, KeyboardInterrupt):
                raise
            except:
                width = int(os.environ.get('COLUMNS', fallback[0]))
                height = int(os.environ.get('COLUMNS', fallback[1]))
            # Work around above returning width, height = 0, 0 in Emacs
            width = width if width != 0 else fallback[0]
            height = height if height != 0 else fallback[1]
            return width, height
        else:
            return get_terminal_size(fallback)

    def _open_editor(self, editcmd):
        """Extra method to allow for easy override in tests."""
        subprocess.Popen(editcmd, shell=True).communicate()

    def _get_current_position(self):
        frame = self.curframe
        lineno = frame.f_lineno
        filename = os.path.abspath(frame.f_code.co_filename)
        return filename, lineno

    def _quote_filename(self, filename):
        try:
            from shlex import quote
        except ImportError:
            from pipes import quote

        return quote(filename)

    def _format_editcmd(self, editor, filename, lineno):
        filename = self._quote_filename(filename)

        if "{filename}" in editor:
            return editor.format(filename=filename, lineno=lineno)

        if "%s" not in editor:
            # backward compatibility.
            return "%s +%d %s" % (editor, lineno, filename)

        # Replace %s with filename, %d with lineno; %% becomes %.
        return editor.replace("%%", "%").replace("%s", filename).replace(
            "%d", str(lineno))

    def _get_editor_cmd(self, filename, lineno):
        editor = self.config.editor
        if editor is None:
            try:
                editor = os.environ["EDITOR"]
            except KeyError:
                try:
                    from shutil import which
                except ImportError:
                    from distutils.spawn import find_executable as which
                editor = which("vim")
                if editor is None:
                    editor = which("vi")
            if not editor:
                raise RuntimeError("Could not detect editor. Configure it or set $EDITOR.")  # noqa: E501
        return self._format_editcmd(editor, filename, lineno)

    def do_edit(self, arg):
        "Open an editor visiting the current file at the current line"
        if arg == '':
            filename, lineno = self._get_current_position()
        else:
            filename, lineno = self._get_fnamelineno_for_arg(arg)
            if filename is None:
                self.error("could not parse filename/lineno")
                return
        # this case handles code generated with py.code.Source()
        # filename is something like '<0-codegen foo.py:18>'
        match = re.match(r'.*<\d+-codegen (.*):(\d+)>', filename)
        if match:
            filename = match.group(1)
            lineno = int(match.group(2))

        try:
            self._open_editor(self._get_editor_cmd(filename, lineno))
        except Exception as exc:
            self.error(exc)

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
                                stdin=subprocess.PIPE)
        proc.stdin.write(text)
        proc.stdin.close()

    def _put(self, text):
        stdin_paste = self.config.stdin_paste
        if stdin_paste is None:
            print('** Error: the "stdin_paste" option is not configured **',
                  file=self.stdout)
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

    def set_step(self):
        """Use set_next() via set_trace() when re-using Pdb instance.

        But call set_step() before still for handling of frame_returning."""
        super(Pdb, self).set_step()
        if hasattr(self, "_set_trace_use_next"):
            del self._set_trace_use_next
            self.set_next(self._via_set_trace_frame)

    def stop_here(self, frame):
        # Always stop at starting frame (https://bugs.python.org/issue38806).
        if self.stopframe is None:
            if getattr(self, "_via_set_trace_frame", None) == frame:
                if not self._stopped_for_set_trace:
                    self._stopped_for_set_trace = True
                    return True
        if Pdb is not None:
            return super(Pdb, self).stop_here(frame)

    def set_trace(self, frame=None):
        """Remember starting frame.

        This is used with pytest, which does not use pdb.set_trace().
        """
        if getattr(local, "_pdbpp_completing", False):
            # Handle set_trace being called during completion, e.g. with
            # fancycompleter's attr_matches.
            return
        if self.disabled:
            return

        if frame is None:
            frame = sys._getframe().f_back
        self._via_set_trace_frame = frame
        self._stopped_for_set_trace = False

        self.start_filename = frame.f_code.co_filename
        self.start_lineno = frame.f_lineno

        return super(Pdb, self).set_trace(frame)

    def is_skipped_module(self, module_name):
        """Backport for https://bugs.python.org/issue36130.

        Fixed in Python 3.8+.
        """
        if module_name is None:
            return False
        return super(Pdb, self).is_skipped_module(module_name)

    def message(self, msg):
        if self.sticky:
            if sys._getframe().f_back.f_code.co_name == "user_exception":
                # Exceptions are handled in sticky mode explicitly.
                return
            self._sticky_messages.append(msg)
            return
        print(msg, file=self.stdout)

    def error(self, msg):
        """Override/enhance default error method to display tracebacks."""
        self._seen_error = msg
        print("***", msg, file=self.stdout)

        if not self.config.show_traceback_on_error:
            return

        etype, evalue, tb = sys.exc_info()
        if tb and tb.tb_frame.f_code.co_name == "default":
            tb = tb.tb_next
            if tb and tb.tb_frame.f_code.co_filename == "<stdin>":
                tb = tb.tb_next
                if tb:  # only display with actual traceback.
                    self._remove_bdb_context(evalue)
                    tb_limit = self.config.show_traceback_on_error_limit
                    fmt_exc = traceback.format_exception(
                        etype, evalue, tb, limit=tb_limit
                    )

                    # Remove last line (exception string again).
                    if len(fmt_exc) > 1 and fmt_exc[-1][0] != " ":
                        fmt_exc.pop()

                    print("".join(fmt_exc).rstrip(), file=self.stdout)

    @staticmethod
    def _remove_bdb_context(evalue):
        """Remove exception context from Pdb from the exception.

        E.g. "AttributeError: 'Pdb' object has no attribute 'do_foo'",
        when trying to look up commands (bpo-36494).
        Only done for Python 3+.
        """
        if not hasattr(evalue, "__context__"):
            return

        removed_bdb_context = evalue
        while removed_bdb_context.__context__:
            ctx = removed_bdb_context.__context__
            if (
                isinstance(ctx, AttributeError)
                and ctx.__traceback__.tb_frame.f_code.co_name == "onecmd"
            ):
                removed_bdb_context.__context__ = None
                break
            removed_bdb_context = removed_bdb_context.__context__


# simplified interface

if hasattr(pdb, 'Restart'):
    Restart = pdb.Restart

if hasattr(pdb, '_usage'):
    _usage = pdb._usage

# copy some functions from pdb.py, but rebind the global dictionary
for name in 'run runeval runctx runcall main set_trace'.split():
    func = getattr(pdb, name)
    globals()[name] = rebind_globals(func, globals())
del name, func


# Post-Mortem interface

def post_mortem(t=None, Pdb=Pdb):
    # handling the default
    if t is None:
        # sys.exc_info() returns (type, value, traceback) if an exception is
        # being handled, otherwise it returns None
        t = sys.exc_info()[2]
    if t is None:
        raise ValueError("A valid traceback must be passed if no "
                         "exception is being handled")

    p = Pdb()
    p.reset()
    p.interaction(None, t)


def pm(Pdb=Pdb):
    post_mortem(sys.last_traceback, Pdb=Pdb)


def cleanup():
    local.GLOBAL_PDB = None
    local._pdbpp_completing = False

# pdb++ specific interface


def xpm(Pdb=Pdb):
    """
    To be used inside an except clause, enter a post-mortem pdb
    related to the just caught exception.
    """
    info = sys.exc_info()
    print(traceback.format_exc())
    post_mortem(info[2], Pdb)


def enable():
    global set_trace
    set_trace = enable.set_trace
    if local.GLOBAL_PDB:
        local.GLOBAL_PDB.disabled = False


enable.set_trace = set_trace


def disable():
    global set_trace
    set_trace = disable.set_trace
    if local.GLOBAL_PDB:
        local.GLOBAL_PDB.disabled = True


disable.set_trace = lambda frame=None, Pdb=Pdb: None


def set_tracex():
    print('PDB!')


set_tracex._dont_inline_ = True

_HIDE_FRAME = object()


def hideframe(func):
    c = func.__code__
    new_co_consts = c.co_consts + (_HIDE_FRAME,)

    if hasattr(c, "replace"):
        # Python 3.8.
        c = c.replace(co_consts=new_co_consts)
    elif sys.version_info < (3, ):
        c = types.CodeType(
            c.co_argcount, c.co_nlocals, c.co_stacksize,
            c.co_flags, c.co_code,
            new_co_consts,
            c.co_names, c.co_varnames, c.co_filename,
            c.co_name, c.co_firstlineno, c.co_lnotab,
            c.co_freevars, c.co_cellvars)
    else:
        # Python 3 takes an additional arg -- kwonlyargcount.
        c = types.CodeType(
            c.co_argcount, c.co_kwonlyargcount, c.co_nlocals, c.co_stacksize,
            c.co_flags, c.co_code,
            c.co_consts + (_HIDE_FRAME,),
            c.co_names, c.co_varnames, c.co_filename,
            c.co_name, c.co_firstlineno, c.co_lnotab,
            c.co_freevars, c.co_cellvars)

    func.__code__ = c
    return func


def always(obj, value):
    return True


def break_on_setattr(attrname, condition=always, Pdb=Pdb):
    def decorator(cls):
        old___setattr__ = cls.__setattr__

        @hideframe
        def __setattr__(self, attr, value):
            if attr == attrname and condition(self, value):
                frame = sys._getframe().f_back
                pdb_ = Pdb()
                pdb_.set_trace(frame)
                pdb_.stopframe = frame
                pdb_.interaction(frame, None)
            old___setattr__(self, attr, value)
        cls.__setattr__ = __setattr__
        return cls
    return decorator


if __name__ == '__main__':
    import pdbpp
    pdbpp.main()

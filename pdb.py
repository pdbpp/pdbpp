"""
A fancier version of pdb.

This module extends the stdlib pdb in numerous ways, e.g. by providing
real completion of Python values instead of pdb's own commands, or by
adding few convenience commands like ``longlist``, ``interact`` or
``watch``.
"""

import sys
import os.path
import readline
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

    line_number_color = colors.turquoise
    current_line_color = 44 # blue

    # WARNING: for this option to work properly, you need to patch readline with this:
    # http://codespeak.net/svn/user/antocuni/hack/readline-escape.patch
    color_complete = False

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

class Pdb(pdb.Pdb, ConfigurableClass):

    DefaultConfig = DefaultConfig
    config_filename = '.pdbrc.py'

    def __init__(self, Config=None):
        pdb.Pdb.__init__(self)
        self.config = self.get_config(Config)
        self.prompt = self.config.prompt
        self.completekey = self.config.completekey
        if self.config.color_complete:
            readline.parse_and_bind('set dont-escape-ctrl-chars on')

        self.mycompleter = None
        self.watching = {} # frame --> (name --> last seen value)
        self.sticky = False
        self.tb_lineno = {} # frame --> lineno where the exception raised

    def interaction(self, frame, traceback, orig_traceback=None):
        self.setup(frame, traceback, orig_traceback)
        self.print_stack_entry(self.stack[self.curindex])
        self.cmdloop()
        self.forget()

    def setup(self, frame, tb, orig_tb=None):
        pdb.Pdb.setup(self, frame, tb)
        tb = orig_tb
        while tb:
            lineno = lasti2lineno(tb.tb_frame.f_code, tb.tb_lasti)
            self.tb_lineno[tb.tb_frame] = lineno
            tb = tb.tb_next

    def forget(self):
        pdb.Pdb.forget(self)
        self.raise_lineno = {}

    def complete(self, text, state):
        if state == 0:
            mydict = self.curframe.f_globals.copy()
            mydict.update(self.curframe.f_locals)
            self.mycompleter = rlcompleter_ng.Completer(mydict)
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
        self.lastcmd = 'longlist'
        self._printlonglist()

    def _printlonglist(self):
        try:
            lines, lineno = getsourcelines(self.curframe)
        except IOError, e:
            print '** Error: %s **' % e
            return

        exc_lineno = self.tb_lineno.get(self.curframe, None)
        lines = [line[:-1] for line in lines] # remove the trailing '\n'
        if self.config.highlight:
            maxlength = max(map(len, lines))
            lines = [line.ljust(maxlength) for line in lines]
            src = self.format_source('\n'.join(lines))
            lines = src.splitlines()
        for i, line in enumerate(lines):
            marker = ''
            if lineno == self.curframe.f_lineno:
                marker = '->'
            elif lineno == exc_lineno:
                marker = '>>'
            lines[i] = self.format_line(lineno, marker, line)
            lineno += 1
        print '\n'.join(lines)

    do_ll = do_longlist


    def do_interact(self, arg):
        ns = self.curframe.f_globals.copy()
        ns.update(self.curframe.f_locals)
        code.interact("*interactive*", local=ns)

    def do_track(self, arg):
        try:
            from pypy.translator.tool.reftracker import track
        except ImportError:
            print '** cannot import pypy.translator.tool.reftracker **'
            return
        val = self._getval(arg)
        track(val)

    def _get_watching(self):
        return self.watching.setdefault(self.curframe, {})

    def do_watch(self, arg):
        try:
            value = self._getval(arg)
        except:
            return
        self._get_watching()[arg] = value

    def do_unwatch(self, arg):
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
            self._printlonglist()

    def do_sticky(self, arg):
        self.sticky = not self.sticky
        self._print_if_sticky()

    def preloop(self):
        self._print_if_sticky()
        watching = self._get_watching()
        for expr, oldvalue in watching.iteritems():
            newvalue = self._getval(expr)
            # check for identity first; this prevents custom __eq__ to
            # be called at every loop, and also prevents instances
            # whose fields are changed to be displayed
            if newvalue is not oldvalue or newvalue != oldvalue:
                watching[expr] = newvalue
                print '%s = %r' % (expr, newvalue)

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
    orig_tb = t
    while t.tb_next is not None:
        t = t.tb_next
    p.interaction(t.tb_frame, t, orig_tb)

# pdb++ specific interface

def xpm(Pdb=Pdb):
    """
    To be used inside an except clause, enter a post-mortem pdb
    related to the just catched exception.
    """
    post_mortem(sys.exc_info()[2], Pdb)

def main():
    bar = 53
    foobar = 12
    foo()

def foo():
    class MyClass:
        def method(self):
            pass
    x = MyClass()
    foobar = int(42)
    foobaz = "foo"
    c = 42
    i = 0
    j = 0
    set_trace()
    for i in range(10):
        if i == 5:
            j = 42

if __name__ == '__main__':
    Pdb.do_l = Pdb.do_longlist # for my personal convenience
    main()


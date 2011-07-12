.. -*- restructuredtext -*-

pdb++, a drop-in replacement for pdb
====================================

What is it?
------------

This module is an extension of the pdb_ module of the standard library.  It is
meant to be fully compatible with its predecessor, yet it introduces a number
of new features to make your debugging experience as nice as possible.

.. image:: http://bitbucket.org/antocuni/pdb/raw/0c86c93cee41/screenshot.png

``pdb++`` features include:

  - colorful TAB completion of Python expressions (through fancycompleter_)

  - optional syntax highlighting of code listings (through pygments_)

  - `sticky mode`_

  - several new commands to be used from the interactive ``(Pdb++)`` prompt

  - `smart command parsing`_ (hint: have you ever typed ``r`` or ``c`` at the
    prompt to print the value of some variable?)

  - additional convenience functions in the ``pdb`` module, to be used from
    your program

``pdb++`` is meant to be a drop-in replacement for ``pdb``. If you find some
unexpected behavior, please report it as a bug.

.. _pdb: http://docs.python.org/library/pdb.html
.. _fancycompleter: http://bitbucket.org/antocuni/fancycompleter
.. _pygments: http://pygments.org/

Installation
-------------

Since ``pdb++`` is not a valid identifier for ``pip`` and ``easy_install``,
you have to install ``pdbpp`` instead::

    $ pip install pdbpp
    
    -- OR --

    $ easy_install pdbpp

Alternatively, you can just put ``pdb.py`` somewhere inside your
``PYTHONPATH``.

Note that the module is called ``pdb.py`` so that ``pdb++`` will automatically
be used in all places that do ``import pdb`` (e.g., ``py.test --pdb`` will
give you a ``pdb++`` prompt).  The old ``pdb`` module is still available by
doing e.g. ``import pdb; pdb.pdb.set_trace()``

New interactive commands
------------------------

The following are new commands that you can use from the interative
``(Pdb++)`` prompt.

.. _`sticky mode`:

``sticky [start end]``
  toggle **sticky mode**.  When in this mode, every time the current position
  change, the screen is repainted and the whole function shown.  Thus, when
  doing step-by-step execution you can easily follow the flow of the
  execution.  If ``start`` and ``end`` are given, sticky mode is enabled and
  only lines within that range (extremes included) will be displayed.


``longlist`` (``ll``)
  List source code for the current function.  Differently than the normal pdb
  ``list`` command, ``longlist`` displays the whole function.  The current
  line is marked with ``->``.  In case of post-mortem debugging, the line
  which effectively raised the exception is marked with ``>>``.  If the
  ``highlight`` `config option`_ is set and pygments_ is installed, the source
  code is highlighted.


``interact``
  Start an interative interpreter whose global namespace contains all the
  names found in the current scope.


``track EXPRESSION``
  display a graph showing which objects are the value of the expression refers
  to and are referred by.  This command requires the ``pypy`` source code to
  be importable.

``display EXPRESSION``
  add an expression to the **display list**; expressions in this list are
  evaluated at each step, and printed every time its value changes.
  **WARNING**: since the expressions is evaluated multiple time, pay attention
  not to put expressions with side-effects in the display list.

``undisplay EXPRESSION``:
  remove ``EXPRESSION`` from the display list.

``source EXPRESSION``
  show the source code for the given function/method/class.

``edit EXPRESSION``
  open the editor in the right position to edit the given
  function/method/class.  The editor to use is specified through a `config
  option`_.

``hf_unhide``, ``hf_hide``, ``hf_list``
  some frames might be marked as "hidden" by e.g. using the `@pdb.hideframe`_
  function decorator.  By default, hidden frames are not shown in the stack
  trace, and cannot be reached using ``up`` and ``down``.  You can use
  ``hf_unhide`` to tell pdb to ignore the hidden status (i.e., to treat hidden
  frames as normal ones), and ``hf_hide`` to hide them again.  ``hf_list``
  prints a list of hidden frames.


Smart command parsing
----------------------

By default, pdb tries hard to interpret what you enter at the command prompt
as one of its builtin commands.  However, this is unconvenient if you want to
just print the value of a local variable which happens to have the same name
as one of such commands. E.g.::

    (Pdb) list
      1  	
      2     def fn():
      3         c = 42
      4         import pdb;pdb.set_trace()
      5  ->     return c
    (Pdb) c

In the example above, instead of printing 42 pdb interprets the command as
``continue``, and then you loose your prompt.  This is even worse because it
happens even if you type e.g. ``c.__class__``.

pdb++ fixes this unfriendly (from the author's point of view, of course :-))
behavior by always giving the precedence to printing the value of the given
variable, if it exists.  If you really want to execute the corresponding
command, you can prefix it with ``!!``.  Thus, the example above becomes::

    (Pdb++) list
      1  	
      2     def fn():
      3         c = 42
      4         import pdb;pdb.set_trace()
      5  ->     return c
    (Pdb++) c
    42
    (Pdb++) !!c

Note that the "smart" behavior takes place only when there is ambiguity,
i.e. if it exists a variable with the same name of a command: in all the other
cases, everything works as usual.

Additional functions in the ``pdb`` module
------------------------------------------

The ``pdb`` module that comes with pdb++ includes all the functions and
classes that are in the module from the standard lib.  If you find any
difference, please report it as a bug.

In addition, there are some new convenience functions that are unique to
pdb++.

``pdb.xpm()``
  eXtended Post Mortem: it is equivalent to
  ``pdb.post_morted(sys.exc_info()[2])``.  If used inside an ``except``
  clause, it will start a post-mortem pdb prompt from the line that raised the
  exception being caught.

``pdb.disable()``
  disable ``pdb.set_trace()``: any subsequent call to it will be ignored.

``pdb.enable()``
  re-enable ``pdb.set_trace()``, in the case it was disabled by ``pdb.disable()``.

.. _`@pdb.hideframe`:

``@pdb.hideframe``
  function decorator to tells pdb++ to hide the frame corresponding to the
  function.  Hidden frames do not show up when using interactive commands such
  as ``up``, ``down`` or ``where``, unless ``hf_unhide`` is invoked.

``@pdb.break_on_setattr(attrname, condition=always)``

  class decorator: break the execution of program every time that the
  attribute ``attrname`` is set on any instance of the class. ``condition`` is
  a callable taking the target object of the ``setattr`` and the actual value;
  by default, it breaks every time the attribute is set. E.g.::

      @break_on_setattr('bar')
      class Foo(object):
          pass
      f = Foo()
      f.bar = 42    # the program breaks here

  If can be used also after the class has already been created, e.g. if we
  want to break when some attribute of a particular object is set::

      class Foo(object):
          pass
      a = Foo()
      b = Foo()
      
      def break_if_a(obj, value):
          return obj is a

      break_on_setattr('bar', condition=break_if_a)(Foo)
      b.bar = 10   # no break
      a.bar = 42   # the program breaks here


Configuration and customization
-------------------------------

.. _`config option`:

To customize pdb++, you can put a file named ``.pdbrc.py`` in your home
directory.  The file must contain a class named ``Config`` inheriting from
``pdb.DefaultConfig`` and overridding the desired values.

The following is a list of the options you can customize, together with their
default value:

``prompt = '(Pdb++) '``
  the prompt to show when in interactive mode.

``highlight = True``
  highlight line numbers and the current line when showing the ``longlist`` of
  a function or when in **sticky mode**.

``encoding = 'utf-8'``
  file encoding. Useful when there are international characters in your string
  literals or comments.

``sticky_by_default = False``
  determine whether pdb++ starts in sticky mode or not.

``line_number_color = Color.turquoise``
  the color to use for line numbers.

``filename_color = Color.yellow``
  the color to use for file names when printing the stack entries

``current_line_color = 44``
  the background color to use to highlight the current line; the background
  color is set by using the ANSI escape sequence ``^[Xm`` where ``^`` is the
  ESC character and ``X`` is the background color. 44 corresponds to "blue".

``use_pygments = True``
  if pygments_ is installed and ``highlight == True``, apply syntax highlight
  to the source code when showing the ``longlist`` of a function or when in
  **sticky mode**.

``bg = 'dark'`` 
  passed directly to ``pygments.formatters.TerminalFormatter`` constructor.
  Select the color scheme to use, depending on the background color of your
  terminal. If you have a light background color, try to set it to
  ``'light'``.

``colorscheme = None``
  passed directly to ``pygments.formatters.TerminalFormatter`` constructor.
  A dictionary mapping token types to (lightbg, darkbg) color names or
  ``None`` (default: ``None`` = use builtin colorscheme).

``editor = '${EDITOR:-vi}'``
  the command to invoke when using the ``edit`` command. By default, it uses
  ``$EDITOR`` if set, else ``vi``.  The command must support the standard
  notation ``COMMAND +n filename`` to open filename at line ``n``.  At least
  ``emacs`` and ``vi`` do support it.

``truncate_long_lines = True``
  truncate lines which exceeds the terminal width.

``exec_if_unfocused = None``
  shell command to execute when starting the pdb prompt and the terminal
  window is not focused.  Useful to e.g. play a sound to alert the user that
  the execution of the program stopped. It requires the wmctrl_ module.

``disable_pytest_capturing = True``
  old versions of `py.test`_ crash when you executed ``pdb.set_trace()`` in a
  test but the standard output is captured (i.e., without the ``-s`` option,
  which is the default behavior).  When this option is on, the stdout
  capturing is automatically disabled before showing the interactive prompt.

``def setup(self, pdb): pass``
  this method is called during the initialization of the ``Pdb`` class. Useful
  to do complex setup.


.. _wmctrl: http://bitbucket.org/antocuni/wmctrl
.. _`py.test`: http://pytest.org

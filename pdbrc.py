import readline
import pdb

# make 'l' an alias to 'longlist'
pdb.Pdb.do_l = pdb.Pdb.do_longlist
pdb.Pdb.do_st = pdb.Pdb.do_sticky

class Config(pdb.DefaultConfig):

    def __init__(self):
        readline.parse_and_bind('set convert-meta on')
        readline.parse_and_bind('Meta-/: complete')

        try:
            from pygments.formatters import terminal
        except ImportError:
            pass
        else:
            self.colorscheme = terminal.TERMINAL_COLORS.copy()
            self.colorscheme.update({
                terminal.Keyword:            ('darkred',     'red'),
                terminal.Number:             ('darkyellow',  'yellow'),
                terminal.String:             ('brown',       'green'),
                terminal.Name.Function:      ('darkgreen',   'blue'),
                terminal.Name.Namespace:     ('teal',        'turquoise'),
                })



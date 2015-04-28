# this file is needed to hijack pdb without eggs
import os.path
exec(compile(open(os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'pdb.py')).read(), os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'pdb.py'), 'exec'))

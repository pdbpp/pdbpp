# this file is needed to hijack pdb without eggs
import os.path
pdb_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'pdb.py')
with open(pdb_path) as f:
    exec(compile(f.read(), pdb_path, 'exec'))

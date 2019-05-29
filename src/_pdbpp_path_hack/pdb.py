# this file is needed to hijack pdb without eggs
import os

if int(os.environ.get('PDBPP_HIJACK_PDB', 1)):
    pdb_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'pdbpp.py')
else:
    # Use original pdb.
    import code  # arbitrary module which stays in the same dir as pdb

    pdb_path = os.path.join(os.path.dirname(code.__file__), 'pdb.py')

# Set __file__ to exec'd code.  This is good in general, and required for
# coverage.py to use it.
__file__ = pdb_path

with open(pdb_path) as f:
    exec(compile(f.read(), pdb_path, 'exec'))

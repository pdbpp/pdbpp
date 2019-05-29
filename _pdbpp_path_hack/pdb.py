# this file is needed to hijack pdb without eggs
import os
import sys

if int(os.environ.get('PDBPP_HIJACK_PDB', 1)):
    pdb_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'pdbpp.py')
else:
    # Use original pdb.
    import code  # arbitrary module which stays in the same dir as pdb

    pdb_path = os.path.join(os.path.dirname(code.__file__), 'pdb.py')

with open(pdb_path) as f:
    exec(compile(f.read(), pdb_path, 'exec'))

# Update/set __file__ attribute to actually sourced file, but not when not coming
# here via "-m pdb", where __name__ is "__main__".
if __name__ != "__main__":
    sys.modules["pdb"].__file__ = pdb_path

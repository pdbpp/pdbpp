# this file is needed to hijack pdb without eggs
import os.path
execfile(os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'pdb.py'))

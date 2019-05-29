def test_inspect_source():
    import inspect
    import pdb
    import os

    assert inspect.findsource(pdb.Pdb)
    assert os.path.basename(inspect.getfile(pdb.Pdb)) == "pdbpp.py"

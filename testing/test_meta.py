import pdbpp
import pytest


def test_mod_attributeerror():
    with pytest.raises(AttributeError) as excinfo:
        pdbpp.doesnotexist
    assert str(excinfo.value) == "module 'pdbpp' has no attribute 'doesnotexist'"

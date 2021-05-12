import sys

import pdbpp
import pytest


def test_mod_attributeerror():
    with pytest.raises(AttributeError) as excinfo:
        pdbpp.doesnotexist
    if sys.version_info >= (3, 5):
        assert str(excinfo.value) == "module 'pdbpp' has no attribute 'doesnotexist'"

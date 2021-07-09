import sys

import pytest


def test_ipython(testdir):
    """Test integration when used with IPython.

    - `up` used to crash due to conflicting `hidden_frames` attribute/method.
    """
    pytest.importorskip("IPython")
    child = testdir.spawn(
        "{} -m IPython --colors=nocolor --simple-prompt".format(
            sys.executable,
        )
    )
    child.sendline("%debug raise ValueError('my_value_error')")
    child.sendline("up")
    child.expect_exact("\r\nipdb> ")
    child.sendline("c")
    child.expect_exact("\r\nValueError: my_value_error\r\n")
    child.expect_exact("\r\nIn [2]: ")
    child.sendeof()
    child.sendline("y")
    assert child.wait() == 0

import pytest
import numpy as np
from <<package>> import <<Component>>
<<pytest_module_skip>>

def test_create():
    obj = <<Component>>(<<py_create_args>>)
    assert obj is not None
<<step_pytest_methods_pure>>
def test_getter_setter():
<<getter_setter_test_py_pure>>

def test_reset():
<<reset_test_py_pure>>
<<lifecycle_pytest_methods_pure>>
"""Benchmark for <<Component>>.

<<bench_run>>
"""

import numpy as np
import pytest

<<class_import>>

<<bench_block_consts>>


@pytest.fixture
def obj():
<<py_new_return_4>>
<<bm_step_py>>
<<bm_steps_py>>
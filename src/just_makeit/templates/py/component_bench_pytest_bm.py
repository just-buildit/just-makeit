"""Benchmark for <<Component>>.

<<bench_run>>
"""

import numpy as np
import pytest

from <<package>> import <<Component>>

<<bench_block_consts>>


@pytest.fixture
def obj():
    return <<Component>>(<<py_create_args>>)
<<bm_step_py>>
<<bm_steps_py>>
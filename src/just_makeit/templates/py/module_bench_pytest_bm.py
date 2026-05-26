"""Benchmark for <<Component>>.

Run: pytest src/<<package>>/<<module>>/benchmarks/bench_<<component>>.py --benchmark-only
"""
import pytest
import numpy as np

from <<package>>.<<module>> import <<Component>>

BLOCK_1K  = 1_024
BLOCK_64K = 65_536


@pytest.fixture
def obj():
    return <<Component>>(<<py_create_args>>)
<<bm_step_py>>
<<bm_steps_py>>
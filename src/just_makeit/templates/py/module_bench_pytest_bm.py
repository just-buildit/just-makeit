"""Benchmark for <<Component>>.

Run: pytest src/<<package>>/<<module>>/benchmarks/bench_<<component>>.py --benchmark-only
"""
import pytest
import numpy as np

from <<package>>.<<module>> import <<Component>>

<<bench_block_consts>>


@pytest.fixture
def obj():
    return <<Component>>(<<py_create_args>>)
<<bm_step_py>>
<<bm_steps_py>>
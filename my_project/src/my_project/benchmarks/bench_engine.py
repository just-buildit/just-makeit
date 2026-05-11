import numpy as np
import pytest

from my_project import Engine


@pytest.fixture
def obj():
    return Engine(1.0)


@pytest.mark.benchmark(group="engine")
def test_bench_step(benchmark, obj):
    benchmark(obj.step, 1.0 + 0.0j)


@pytest.mark.benchmark(group="engine")
def test_bench_steps_1k(benchmark, obj):
    x = np.ones(1024, dtype=np.complex64)
    benchmark(obj.steps, x)


@pytest.mark.benchmark(group="engine")
def test_bench_steps_64k(benchmark, obj):
    x = np.ones(65536, dtype=np.complex64)
    benchmark(obj.steps, x)

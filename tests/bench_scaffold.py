"""
bench_scaffold.py — pytest-benchmark tests for just-makeit scaffold generation.

Run:  make bench
Save: make bench-save
Cmp:  make bench-compare
"""

import pytest

from just_makeit import _init, _new


@pytest.mark.benchmark(group="scaffold")
def test_bench_new(benchmark, tmp_path):
    counter = [0]

    def run():
        counter[0] += 1
        root = tmp_path / f"proj_{counter[0]}"
        _new.run("my_proj", dest=root)

    benchmark(run)


@pytest.mark.benchmark(group="scaffold")
def test_bench_new_with_component(benchmark, tmp_path):
    counter = [0]

    def run():
        counter[0] += 1
        root = tmp_path / f"proj_{counter[0]}"
        _new.run(
            "my_proj",
            dest=root,
            object_names=["engine"],
            state_vars=[("gain", "double", "1.0")],
        )

    benchmark(run)


@pytest.mark.benchmark(group="scaffold")
def test_bench_init(benchmark, tmp_path):
    counter = [0]

    def setup():
        counter[0] += 1
        root = tmp_path / f"init_{counter[0]}"
        _new.run("my_proj", dest=root)
        return (root,), {
            "state_vars": [("gain", "double", "1.0")],
            "_hint": False,
        }

    benchmark.pedantic(
        lambda root, **kw: _init.run(root, "engine", **kw),
        setup=setup,
        rounds=20,
        warmup_rounds=3,
    )


@pytest.mark.benchmark(group="scaffold")
def test_bench_new_multistate(benchmark, tmp_path):
    counter = [0]
    state = [
        ("center_freq", "double", "1000.0"),
        ("bandwidth", "double", "200.0"),
        ("order", "int", "4"),
    ]

    def run():
        counter[0] += 1
        root = tmp_path / f"proj_{counter[0]}"
        _new.run("my_dsp", dest=root, object_names=["bpf"], state_vars=state)

    benchmark(run)

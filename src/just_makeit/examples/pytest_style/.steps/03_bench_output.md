## 3. Generated bench file (pytest-benchmark)

`src/dsp_algo/benchmarks/bench_dsp_algo.py` uses `pytest-benchmark` fixtures
instead of a `time.perf_counter()` loop:

```{03_bench_dsp_algo.py}
```

Run it with:

```sh
pytest src/dsp_algo/benchmarks/ --benchmark-only
```

Or include benchmarks in a full test run and use `--benchmark-skip` to skip
them by default:

```sh
pytest --benchmark-skip    # normal CI run
pytest --benchmark-only    # benchmark-only run
```

# pytest_style — pure pytest tests and pytest-benchmark

Demonstrates the `--pytest` and `--pytest-benchmark` flags introduced in
just-makeit 0.11.

By default, just-makeit generates test files based on `unittest.TestCase` with
a pytest compatibility shim (so they work under both runners), and standalone
`bench_*.py` scripts that time with `time.perf_counter()`.

Pass `--pytest` to get pure pytest functions instead — no `unittest` import,
no compatibility shim, `pytest.approx` and `pytest.raises` used directly.

Pass `--pytest-benchmark` to get `pytest-benchmark` fixture-style bench files
instead of standalone timing scripts.

Both flags are project-level: set once on `just-makeit new`, inherited
automatically by every subsequent `just-makeit object` call.

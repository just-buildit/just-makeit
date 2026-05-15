## 2. Generated test file (pure pytest)

`src/dsp_algo/tests/test_dsp_algo.py` contains pure pytest functions with no
`unittest` import and no compatibility shim:

```{02_test_dsp_algo.py}
```

Key differences from the default output:
- No `import unittest` or `class TestDspAlgo(unittest.TestCase)`
- `pytest.approx` instead of the `_approx` shim alias
- `pytest.raises` instead of the `_raises` shim alias
- Plain `assert` — no `self.assertEqual` / `self.assertIs`

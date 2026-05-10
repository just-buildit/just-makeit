## 6. Use from Python

```{06_demo.py}
```

Both types come from the same import:

```python
from my_filters.filter import Fir, Biquad
```

`Fir` and `Biquad` are fully independent — no shared state, separate
`create`/`destroy` lifecycles, each with its own `step`, `steps`, `reset`,
and context manager support.

### Adding a third type later

```sh
just-makeit object iir --module filter \
    --state "sos:double[20]" \
    --state "zi:double[10]"
```

`filter_ext.c`, `filter/CMakeLists.txt`, and `filter/__init__.py` are all
regenerated automatically.  `Fir` and `Biquad` are unaffected — the module
`_ext.c` is always rebuilt from the full object list, not patched.

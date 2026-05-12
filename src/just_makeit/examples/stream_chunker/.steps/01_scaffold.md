## 1. Scaffold

```{01_scaffold.sh}
```

`--no-step` suppresses `step()` and `steps()` — the only interface is `push()`.

`--variable-output` generates two C stubs in `chunker_core.c`:

| Stub | Called by ext | Your job |
|------|---------------|----------|
| `chunker_push_max_out(state)` | Once at `__init__` | Return max output samples possible |
| `chunker_push(state, in, n_in, out)` | Every Python call | Fill `out[]`, return actual count |

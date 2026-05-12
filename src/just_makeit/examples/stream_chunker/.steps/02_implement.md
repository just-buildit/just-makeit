## 2. Implement

Replace both stubs in `native/src/chunker/chunker_core.c`.

Generated stubs:

```{02_push_before.c}
```

Implementation:

```{02_push_after.c}
```

`memcpy` and `complex.h` are already included via `clib_common.h`.

The patch script automates this replacement:

```sh
python3 .steps/02_patch.py
```

## 3. Implement

Two stubs need bodies:

- `filter_step` in `native/inc/filter/filter_core.h` — multiply input by gain.
- `filter_configure` in `native/src/filter/filter_configure_core.c` — parse
  the `gain=` keyword argument and write it to state.

```{03_patch.py}
```

`filter_step` — one multiply:

```{03_step.c}
```

`filter_configure` — parse `gain=` with `PyArg_ParseTupleAndKeywords`:

```{03_configure.c}
```

`PyArg_ParseTupleAndKeywords` accepts the same format characters as
`PyArg_ParseTuple`.  The `|` marks everything that follows as optional, so
`f.configure()` with no arguments is valid and leaves the gain unchanged.
The static `kwlist` array controls which keyword names are accepted and
enables `TypeError` on unknown keywords.

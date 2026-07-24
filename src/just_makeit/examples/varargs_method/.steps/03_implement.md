## 3. Implement

Three stubs need bodies:

- `filter_step` in `native/inc/filter/filter_core.h` — multiply input by gain.
- `filter_configure` in `native/src/filter/filter_configure_core.c` — parse
  the `gain=` keyword argument and write it to state.
- `filter_current_gain` in `native/src/filter/filter_core.c` — return
  `state->gain`.

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

### Document once, in C — rich stubs and a runnable doctest

The sacred header is also the single source of truth for **documentation**. A
Doxygen `/** ... */` comment on `create()` or a *header-declared* method flows
straight into the generated `.pyi` docstring, and a `@code` block becomes a
**runnable doctest**.

This is exactly where the `--varargs` trade-off shows up. `configure()`'s
binding lives in `filter_configure_core.c` — a `PyObject *` file, not the
header — so jm has no declaration to attach docs to, and its stub stays the
bare `configure(*args, **kwargs) -> Any`. The typed `current_gain()`, declared
in `filter_core.h`, is fully documentable. Add a comment to it — the `@code`
doctest deliberately drives `configure()` so both faces of the object are
exercised from one example:

```c
/**
 * @brief Return the filter's current gain coefficient.
 *
 * The typed, self-documenting companion to the flexible varargs
 * configure(): configure() writes the gain, current_gain() reads it
 * back.
 * @return The gain most recently set by the constructor or configure().
 * @code
 * >>> from my_filter import Filter
 * >>> f = Filter(gain=1.0)
 * >>> f.configure(gain=6.0)
 * >>> f.current_gain()
 * 6.0
 * @endcode
 */
double filter_current_gain(filter_state_t *state);
```

`just-makeit apply` re-derives the stub, and `src/my_filter/filter.pyi` now
carries the full numpy-style docstring — including the `@code` block as an
`Examples` doctest:

```python
    def current_gain(self) -> float:
        """Return the filter's current gain coefficient.

        Returns
        -------
        float
            The gain most recently set by the constructor or configure().

        Examples
        --------
        >>> from my_filter import Filter
        >>> f = Filter(gain=1.0)
        >>> f.configure(gain=6.0)
        >>> f.current_gain()
        6.0

        """
```

That doctest is not decoration: it runs against the *built* extension, so if
the kernel ever drifts from its documented example the build fails. Pass `-v`
to watch every `>>>` line execute:

```termynal
$ python -m doctest -v src/my_filter/filter.pyi
{d}Trying:{/d}
    f = Filter(gain=1.0)
{d}Expecting nothing{/d}
{g}ok{/g}
{d}Trying:{/d}
    f.configure(gain=6.0)
{d}Expecting nothing{/d}
{g}ok{/g}
{d}Trying:{/d}
    f.current_gain()
{d}Expecting:{/d}
    6.0
{g}ok{/g}
{d}...{/d}
{g}Test passed.{/g}
```

In CI the whole suite is driven at once with
`pytest --doctest-glob='*.pyi'`.

The enrichment is scripted (it stamps the project's package name into the
doctest import automatically):

```sh
python3 .steps/04b_doxygen.py
just-makeit apply
```

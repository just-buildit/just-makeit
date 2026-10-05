## 3. Implement

Three stubs need bodies:

- `va_filter_filter_step` in `native/inc/va_filter/filter/filter_core.h` — multiply input by gain.
- `va_filter_filter_configure` in `native/src/filter/filter_configure_core.c` — parse
  the `gain=` keyword argument and write it to state.
- `va_filter_filter_current_gain` in `native/src/filter/filter_core.c` — return
  `state->gain`.

`va_filter_filter_step` — one multiply:

```{03_step.c}
```

`va_filter_filter_configure` — parse `gain=` with `PyArg_ParseTupleAndKeywords`
(this is the whole of `filter_configure_core.c`):

```{03_configure.c}
```

`PyArg_ParseTupleAndKeywords` accepts the same format characters as
`PyArg_ParseTuple`.  The `|` marks everything that follows as optional, so
`f.configure()` with no arguments is valid and leaves the gain unchanged.
The static `kwlist` array controls which keyword names are accepted and
enables `TypeError` on unknown keywords.

Paste those bodies in by hand, or let the script below do it; it is the one
this example's own test runs. It reads the two C snippets from its own
directory, so save all three files — `03_patch.py`, `03_step.c` and
`03_configure.c` — in the directory you ran `just-makeit new` from, next to
`va_filter/` rather than inside it: `just-makeit apply` reads every `.c` file
under the project, and refuses a second definition of a name it derives, such
as `va_filter_filter_step`. Then, from the project root:

```sh
python3 ../03_patch.py
```

```{03_patch.py}
```

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
 * >>> from va_filter import Filter
 * >>> f = Filter(gain=1.0)
 * >>> f.configure(gain=6.0)
 * >>> f.current_gain()
 * 6.0
 * @endcode
 */
double va_filter_filter_current_gain(va_filter_filter_state_t *state);
```

The enrichment is scripted. The script also replaces the scaffold `@brief` on
`va_filter_filter_create()`, which becomes the class docstring, and stamps the
project's package name into the doctest import. Save it as `04b_doxygen.py`
next to `va_filter/`, as above:

```{04b_doxygen.py}
```

Then, from the project root:

```sh
python3 ../04b_doxygen.py
just-makeit apply
```

`just-makeit apply` re-derives the stub, and `src/va_filter/filter.pyi` now
carries the full numpy-style docstring — including the `@code` block as an
`Examples` doctest:

```python
    def current_gain(self) -> float:
        """Return the filter's current gain coefficient.

        The typed, self-documenting companion to the flexible varargs
        configure(): configure() writes the gain, current_gain() reads it back.

        Returns
        -------
        float
            The gain most recently set by the constructor or configure().

        Examples
        --------
        >>> from va_filter import Filter
        >>> f = Filter(gain=1.0)
        >>> f.configure(gain=6.0)
        >>> f.current_gain()
        6.0

        """
```

That doctest is not decoration: run against the *built* extension, it fails
the moment the kernel drifts from its documented example. A generated
project's `make test` does not run `.pyi` doctests (this example's own test
does, with `pytest --doctest-glob='*.pyi'`), so to make it a gate in your
project add `PYTHONPATH=src python -m pytest --doctest-glob='*.pyi' src/` to
your test step. Once step 4 has built the extension, pass `-v` to watch every
`>>>` line execute:

```termynal
$ PYTHONPATH=src python -m doctest -v src/va_filter/filter.pyi
{d}...{/d}
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

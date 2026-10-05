## 6. Document once, in C — rich stubs and runnable doctests

The sacred header is also the single source of truth for **documentation**. A
Doxygen `/** ... */` comment on `create()` or a named method flows straight into
the generated `.pyi` docstring, and a `@code` block on a method becomes a
**runnable doctest**. Give `my_arrays_ema_quantize` a real body in
`native/src/ema/ema_core.c`:

```c
uint32_t
my_arrays_ema_quantize(my_arrays_ema_state_t *state, float x)
{
    (void)state;
    if (x <= 0.0f)
        return 0U;
    return (uint32_t)(x + 0.5f);
}
```

and a comment above its declaration in `native/inc/my_arrays/ema/ema_core.h`:

```c
/**
 * @brief Quantize one sample to an unsigned integer code.
 * @param x  Input sample; values <= 0 map to 0.
 * @return Nearest non-negative integer to x (round half up).
 * @code
 * >>> from my_arrays import Ema
 * >>> e = Ema()
 * >>> e.quantize(3.4)
 * 3
 * >>> e.quantize(3.6)
 * 4
 * @endcode
 */
uint32_t my_arrays_ema_quantize(my_arrays_ema_state_t *state, float x);
```

`jm apply` re-derives the stub, and `src/my_arrays/ema.pyi` now carries the full
numpy-style docstring — including the `@code` block as an `Examples` doctest:

```python
    def quantize(self, x: float) -> int:
        """Quantize one sample to an unsigned integer code.

        Parameters
        ----------
        x : float
            Input sample; values <= 0 map to 0.

        Returns
        -------
        int
            Nearest non-negative integer to x (round half up).

        Examples
        --------
        >>> from my_arrays import Ema
        >>> e = Ema()
        >>> e.quantize(3.4)
        3
        >>> e.quantize(3.6)
        4

        """
```

That doctest is not decoration: run against the *built* extension, it fails
the moment the kernel drifts from its documented example. A generated
project's `make test` does not run `.pyi` doctests (this example's own test
does), so to make it a gate in your project add
`PYTHONPATH=src python -m pytest --doctest-glob='*.pyi' src/` to your test
step. To watch every `>>>` line execute, run `doctest -v` after `make`:

```termynal
$ PYTHONPATH=src python -m doctest -v src/my_arrays/ema.pyi
{d}...{/d}
{d}Trying:{/d}
    e = Ema()
{d}Expecting nothing{/d}
{g}ok{/g}
{d}Trying:{/d}
    e.quantize(3.4)
{d}Expecting:{/d}
    3
{g}ok{/g}
{d}Trying:{/d}
    e.quantize(3.6)
{d}Expecting:{/d}
    4
{g}ok{/g}
{d}...{/d}
{g}10 passed and 0 failed.{/g}
{g}Test passed.{/g}
```

That summary is Python 3.12's; 3.13 and later print `10 passed.` instead.
jm's own CI runs this stub's doctests with `pytest --doctest-glob='*.pyi'`,
the same command as above.

## 6. Document once, in C — rich stubs and runnable doctests

The sacred header is also the single source of truth for **documentation**. A
Doxygen `/** ... */` comment on `create()` or a named method flows straight into
the generated `.pyi` docstring, and a `@code` block on a method becomes a
**runnable doctest**. Give `ema_quantize` a real body and a comment:

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
uint32_t ema_quantize(ema_state_t *state, float x);
```

`jm apply` re-derives the stub, and `src/my_arrays/ema.pyi` now carries the full
numpy-style docstring — including the `@code` block as an `Examples` doctest:

```python
    def quantize(self, x: float) -> int:
        """Quantize one sample to an unsigned integer code.

        Parameters
        ----------
        x
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

That doctest is not decoration: it runs against the *built* extension, so if
the kernel ever drifts from its documented example the build fails. Pass `-v`
to watch every `>>>` line execute:

```termynal
$ python -m doctest -v src/my_arrays/ema.pyi
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

In CI the whole suite is driven at once with `pytest --doctest-glob='*.pyi'`.

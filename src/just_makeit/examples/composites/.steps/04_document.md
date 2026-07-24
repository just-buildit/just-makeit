## Document once, in C

The vendored header is not only the resource's API — it is the single source of
truth for the class's **documentation**. A Doxygen `/** ... */` comment on a
`ringbuf_*` function flows straight into the generated `ring.pyi`, and a `@code`
block on a method becomes a **runnable doctest**. Nothing is written twice.

Three functions map to three documentation surfaces on the `Ring` class:

- **`ringbuf_open`** (the `create_fn`) documents the **class**: its `@brief`
  becomes the `Ring` summary and its `@param` annotates the constructor
  parameter.
- **`ringbuf_push`** (a method `fn`) documents the **method** `push`: its
  `@brief`/`@param`/`@return` become the numpy prose, and its `@code` block
  becomes a runnable `Examples` doctest.
- **`ringbuf_get_gain`** (a single-field getter `fn`) documents the **property**
  `gain`: its `@brief` becomes the property docstring.

Document `ringbuf_push`:

```c
/**
 * @brief Append samples to the buffer, scaling each by the current gain.
 * @param x  Samples to append (oldest-to-newest).
 * @return The number of samples accepted; fewer than requested once full.
 * @code
 * >>> import numpy as np
 * >>> from composites.ring import Ring
 * >>> r = Ring(capacity=4)
 * >>> r.push(np.array([1, 2, 3, 4, 5, 6], np.float32))
 * 4
 * @endcode
 */
size_t ringbuf_push (ringbuf_t *r, const float *x, size_t n);
```

`jm apply` re-derives the stub, and `Ring.push` in `ring.pyi` now carries the
full numpy-style docstring — including the `@code` block as an `Examples`
doctest:

```python
    def push(self, x: NDArray[Any]) -> int:
        """Append samples to the buffer, scaling each by the current gain.

        Parameters
        ----------
        x : NDArray[Any]
            Samples to append (oldest-to-newest).

        Returns
        -------
        int
            The number of samples accepted; fewer than requested once full.

        Examples
        --------
        >>> import numpy as np
        >>> from composites.ring import Ring
        >>> r = Ring(capacity=4)
        >>> r.push(np.array([1, 2, 3, 4, 5, 6], np.float32))
        4

        """
```

That doctest is not decoration: it runs against the *built* extension, so if the
ring buffer's capacity handling ever drifts from its documented example the
build fails. Pass `-v` to watch every `>>>` line execute:

```termynal
$ python -m doctest -v src/composites/ring.pyi
{d}Trying:{/d}
    r = Ring(capacity=4)
{d}Expecting nothing{/d}
{g}ok{/g}
{d}Trying:{/d}
    r.push(np.array([1, 2, 3, 4, 5, 6], np.float32))
{d}Expecting:{/d}
    4
{g}ok{/g}
{g}5 passed and 0 failed.{/g}
{g}Test passed.{/g}
```

In CI the whole suite is driven at once with `pytest --doctest-glob='*.pyi'`.

**Not every surface comes from the header.** A **multi-field** getter carries a
single struct `@brief` that cannot name each field it decodes, so the
`used` / `fill_fraction` properties keep their manifest-synthesized docstrings —
the same header-documents-the-C, manifest-documents-the-rest split the
`views_module` example draws. The header documents each C function once; the
manifest fills in what has no C function of its own.

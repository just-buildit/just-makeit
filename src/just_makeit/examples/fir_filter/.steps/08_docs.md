## 8. Document once, in the header

The `@brief` on `fir_filter_create()` in the sacred header is the single source
of truth for the class docstring — edit it and `jm apply` re-derives the `.pyi`
summary from it, so the stub reads like real documentation instead of the
generic "FirFilter component." fallback:

```{08_doxygen.py}
```

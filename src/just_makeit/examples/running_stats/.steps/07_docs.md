## 7. Give the Python class a real docstring

The header is the single source of truth for docs, so replacing the scaffold's
boilerplate `@brief` on `running_stats_create()` with a one-line description
turns the generated `.pyi` class summary from the generic
`"RunningStats component."` into a sentence that says what the object does:

```{07_doxygen.py}
```

Run it, then `jm apply` re-derives the `.pyi` from the edited header.

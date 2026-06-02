## 5. Use from Python

```{05_demo.py}
```

`configure()` accepts `gain=` as a keyword or as a positional — both work
because `PyArg_ParseTupleAndKeywords` handles either calling convention.
Calling it with no arguments (`f.configure()`) is explicitly supported by the
`|` prefix in the format string and leaves the gain unchanged.

## 5. Use from Python

```{05_demo.py}
```

Save it as `05_demo.py` next to `va_filter/`, like the step 3 scripts, and run
it from the project root (it imports the extension from `src/`):

```sh
python3 ../05_demo.py
# configure: PASSED
```

`configure()` accepts `gain=` as a keyword or as a positional — both work
because `PyArg_ParseTupleAndKeywords` handles either calling convention.
Calling it with no arguments (`f.configure()`) is explicitly supported by the
`|` prefix in the format string and leaves the gain unchanged.

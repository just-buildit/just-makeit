## 7. Give the Python class a real docstring

The header is the single source of truth for docs, so replacing the scaffold's
boilerplate `@brief` on `my_stats_running_stats_create()` with a one-line description
turns the generated `.pyi` class summary from the generic
`"RunningStats component."` into a sentence that says what the object does:

```{07_doxygen.py}
```

The script ships with just-makeit, in this example's `.steps/` directory.
Run it from the project root by that path; `jm apply` then re-derives the
`.pyi` from the edited header:

```sh
STEPS="$(python3 -c 'import just_makeit, pathlib; print(pathlib.Path(just_makeit.__file__).parent / "examples/running_stats/.steps")')"
python3 "$STEPS/07_doxygen.py"
just-makeit apply
```

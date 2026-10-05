## 8. Document once, in the header

The `@brief` on `my_fir_fir_filter_create()` in the sacred header is the single source
of truth for the class docstring — edit it and `jm apply` re-derives the `.pyi`
summary from it, so the stub reads like real documentation instead of the
generic "FirFilter component." fallback:

```{08_doxygen.py}
```

The script ships with just-makeit, in this example's `.steps/` directory.
Run it from the project root by that path, then `jm apply`:

```sh
STEPS="$(python3 -c 'import just_makeit, pathlib; print(pathlib.Path(just_makeit.__file__).parent / "examples/fir_filter/.steps")')"
python3 "$STEPS/08_doxygen.py"
just-makeit apply
```

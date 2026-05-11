## 8. Build a wheel

```{08_wheel.sh}
```

`just-makeit build` runs CMake in release mode, packages the `.so` and Python
sources into a PEP 427 wheel, and writes it to `dist/`:

```
dist/iqfile-0.1.0-cp312-cp312-linux_x86_64.whl
```

Install it anywhere:

```sh
pip install dist/iqfile-*.whl
```

Or publish to PyPI:

```sh
pip install twine
twine upload dist/*
```

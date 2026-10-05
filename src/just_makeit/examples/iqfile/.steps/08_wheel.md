## 8. Build a wheel

`just-makeit build` imports just-buildit from just-makeit's own environment
and, on Linux, repairs the wheel with `uvx auditwheel repair`, which needs
`uv` and `patchelf` on `PATH`. Neither install route above installs
just-buildit or uv, and only the `pip` route's `install-deps` installs
patchelf, so add what is missing first (with the venv from Prerequisites
active):

```sh
pip install just-buildit uv     # into the environment just-makeit runs from
sudo apt install patchelf       # Linux, if absent (dnf/pacman/zypper: patchelf)
```

Then:

```{08_wheel.sh}
```

It builds the extension with CMake (reusing `build/` from step 5), packages
the `.so` and Python sources into a PEP 427 wheel, repairs it, and writes it
to `dist/` -- on Linux x86-64 with CPython 3.12, for example:

```
dist/iqfile-0.1.0-cp312-cp312-manylinux1_x86_64.manylinux_2_5_x86_64.whl
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

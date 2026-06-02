# Troubleshooting

Quick-reference for the most common build and runtime failures.

______________________________________________________________________

## CMake not found or wrong version

**Symptom:** `cmake: command not found` or `CMake 3.X, but required is at least 3.16`

**Cause:** CMake is missing or too old.

**Fix:**

```sh
# Linux (Debian/Ubuntu)
sudo apt-get install cmake

# macOS
brew install cmake

# Windows (MSYS2/MinGW)
pacman -S mingw-w64-x86_64-cmake

# Or let the installer handle it:
just-makeit install-deps
```

Verify: `cmake --version` — must print 3.16 or higher.

______________________________________________________________________

## NumPy headers missing

**Symptom:** `fatal error: 'numpy/arrayobject.h' file not found` during
`cmake --build`.

**Cause:** NumPy is installed but CMake can't find its headers. Typically
happens when the venv's site-packages isn't on `CMAKE_PREFIX_PATH`.

**Fix:** Make sure you're building inside the activated venv:

```sh
source .venv/bin/activate   # or the path printed by install.sh
make
```

If the venv is active and the error persists, confirm NumPy is installed:

```sh
python -c "import numpy; print(numpy.get_include())"
```

Pass the output as the include path if CMake still can't find it:

```sh
cmake -B build -DNUMPY_INCLUDE_DIR=$(python -c "import numpy; print(numpy.get_include())")
cmake --build build
```

______________________________________________________________________

## Linker drops the extension module (`--as-needed`)

**Symptom:** `make test` passes but `import my_project` raises
`ImportError: undefined symbol` or `cannot open shared object file`.

**Cause:** GNU ld on Debian/Ubuntu uses `--as-needed` by default. If the
library appears on the command line *before* the object files that reference
it, the linker silently drops it.

**Fix — pkg-config consumers:** split `--cflags` and `--libs`, with the source
file between them:

```sh
# WRONG — library before source
gcc $(pkg-config --cflags --libs my-project) consumer.c -o consumer

# CORRECT
gcc $(pkg-config --cflags my-project) consumer.c \
    $(pkg-config --libs my-project) -lm -o consumer
```

For `make && make test` on the generated project itself, the generated
`CMakeLists.txt` handles link order correctly — this issue only bites external
C consumers.

______________________________________________________________________

## `PKG_CONFIG_PATH` not set for custom prefix

**Symptom:** `pkg-config --cflags my-project` prints nothing or exits with
`Package my-project was not found in the pkg-config search path`.

**Cause:** You installed to a non-standard prefix (e.g. `$HOME/.local`) and
pkg-config doesn't search it by default.

**Fix:**

```sh
export PKG_CONFIG_PATH="$HOME/.local/lib/pkgconfig:$PKG_CONFIG_PATH"
pkg-config --modversion my-project   # should print the version
```

Add the `export` line to your shell profile to persist it.

______________________________________________________________________

## Extension not importable after build (rpath / `LD_LIBRARY_PATH`)

**Symptom:** `python -c "import my_project"` fails with
`libmy_project.so: cannot open shared object file`.

**Cause:** The `.so` was installed to a non-standard prefix and the dynamic
linker can't find it.

**Fix (quick — testing only):**

```sh
export LD_LIBRARY_PATH="$HOME/.local/lib:$LD_LIBRARY_PATH"
python -c "import my_project"
```

**Fix (deployment — embed rpath at link time):**

```cmake
set_target_properties(consumer PROPERTIES INSTALL_RPATH_USE_LINK_PATH ON)
```

Or pass `-DCMAKE_BUILD_RPATH="$HOME/.local/lib"` when configuring.

See [C library — Runtime loading](c-library.md#runtime-loading-rpath) for
the full explanation.

______________________________________________________________________

## Windows: `make.exe` not found

**Symptom:** `'make' is not recognized as an internal or external command`
on Windows.

**Cause:** MinGW ships `mingw32-make.exe`, not `make.exe`.

**Fix:**

```sh
# In the MinGW shell, copy the binary:
cp "$(which mingw32-make)" "$(dirname $(which mingw32-make))/make.exe"
```

Or use the Docker image, which has this already resolved:

```sh
docker run --rm -it ghcr.io/just-buildit/jm-examples-windows:latest
```

______________________________________________________________________

## Generated project fails to import after `pip install -e .`

**Symptom:** `from my_project import Engine` raises `ModuleNotFoundError`
after an editable install.

**Cause:** The editable install points Python at `src/`, but the compiled
`.so` must be built first — `pip install -e .` does not build C code.

**Fix:**

```sh
make        # builds the .so and places it in src/my_project/
pip install -e .
```

After this, Python-only edits take effect immediately; rebuild with `make`
after any C changes.

______________________________________________________________________

## I edited `just-makeit.toml` but `_core.c` didn't change

**Symptom:** you changed a method's signature (or a state field) in the TOML,
ran `jm apply`, but `<comp>_core.c` still has the old body.

**Cause:** this is by design. `_core.c` is **sacred** — `jm apply` never
splices or re-renders it. Apply regenerates the glue (`_ext.c`, `.pyi`,
`CMakeLists.txt`) and injects any missing method/property *declaration* into
`_core.h`, but your hand-written `steps()` and lifecycle bodies are yours to
keep.

**Fix:** for a new method or computed property, the additive verb (`jm method`,
`jm property`) injects a declaration and appends a fresh stub for you to fill
in. A signature change or a new state field is structural — rebuild from the
manifest with `jm regenerate` (or `jm add` for state). The rebuild discards
hand-written `_core.c` bodies, so stash first (or keep them in the TOML
`impl`/`create_impl`):

```sh
git stash
just-makeit regenerate <comp>   # deletes the component's files, re-runs apply
```

`regenerate` leaves the manifest untouched (unlike `jm remove`).

______________________________________________________________________

## Generated header has `const T *` on a parameter that my function writes into

**Symptom:** The generated (or refreshed) `_core.h` declares a function
parameter as `const float *w` but the implementation writes into `w`, producing
a clang-tidy / cppcheck warning or a confusing mismatch between header and body.

**Cause:** Every array parameter (`T[]`) is `const T *` by default — jm treats
it as read-only input. A parameter that the function *writes into* must be
explicitly marked as an output buffer.

**Fix:** add `out = true` to the parameter in the manifest. For a module
function:

```toml
[[spectral.functions]]
name = "kaiser_window"
return_type = "void"

[[spectral.functions.params]]
name = "w"
type = "float[]"
out = true        # drops const → float *w in C

[[spectral.functions.params]]
name = "beta"
type = "float"
```

For a method, use `--out-param w:float[]` on the CLI:

```sh
just-makeit method spectral kaiser_window --out-param w:float[] --param beta:float
```

After updating the TOML, run `jm apply` to refresh the declaration in
`_core.h`.

______________________________________________________________________

## `--return-type "T[]"` is rejected

**Symptom:** `just-makeit object x --return-type "float[]"` exits with an error
about array return types.

**Cause:** array *return* ("blockwise", `T[] -> T[]`) is not yet supported.
The error is deliberate — earlier versions crashed instead.

**Fix:** array *input* works (`--arg-type "float[]"`), and the result is a
single sample. If you need block-out, return into an `out=` buffer via a
`multi_output` method, or emit through a `variable_output` method. There is no
`blockwise` preset for the same reason.

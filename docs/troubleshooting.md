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

Windows is opt-in (jm targets Linux/macOS by default); scaffold a
Windows-targeting project with `jm new --windows`. See
[`[project] platforms`](commands/scaffold.md).

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
manifest with `jm regenerate` (or `jm add` for state, which always does a
discarding rebuild since the old body's signature is already stale). By
default `jm regenerate` lifts hand-written `_core.c`/`_core.h` bodies before
deleting the files and splices them back into the fresh scaffold — pass
`--discard` for a clean reset instead. Either way the splice is best-effort
text matching, not a guarantee, so stash first (or keep the algorithm in the
TOML `impl`/`create_impl`, which the rebuild reasserts):

```sh
git stash
just-makeit regenerate <comp>   # deletes the component's files, re-runs apply
```

`regenerate` leaves the manifest untouched (unlike `jm remove`).

______________________________________________________________________

## I changed a method's shape in the TOML but its binding kept the old one

**Symptom:** you added a `param` to an existing `[[<obj>.methods]]` entry (or
changed a param's type), ran `jm apply`, and the generated binding in
`native/src/<mod>/<mod>_ext_<obj>.c` still has the old signature. No error, no
warning — it just quietly keeps generating the previous shape.

**Cause:** the per-object ext fragment is **sacred**, same contract as
`_core.c` above. `jm apply` is additive: it materializes files and methods
that are *missing*, and reconciles wiring — it does not re-render a binding
that already exists. So the shape frozen at the method's first `apply` is the
one you keep.

The asymmetry is easy to trip over, because adding a *new* method to the
manifest does work on the next `apply` — only re-shaping an existing one is a
no-op.

**Fix:** delete the fragment and re-apply, which is jm's sanctioned migration
mechanic (the manifest is the source of truth, so the glue can always be
rebuilt from it):

```sh
rm native/src/<mod>/<mod>_ext_<obj>.c
just-makeit apply
```

For a standalone (non-module) object the file is
`native/src/<obj>/<obj>_ext.c`. Your `_core.c` algorithm is untouched either
way — only the generated glue is rebuilt.

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
[[module.spectral.functions]]
name = "kaiser_window"
return_type = "void"

[[module.spectral.functions.params]]
name = "w"
type = "float[]"
out = true        # drops const → float *w in C

[[module.spectral.functions.params]]
name = "beta"
type = "float"
```

For a module function, use `--out-param w:float[]` on the CLI (`--out-param`
is `jm function`-only; `jm method` has no equivalent flag):

```sh
just-makeit function kaiser_window --module spectral --out-param w:float[] --param beta:float
```

After updating the TOML, run `jm apply` to refresh the declaration in
`_core.h`.

______________________________________________________________________

## `--return-type "T[]"` requires an array `--arg-type`

**Symptom:** `just-makeit object x --return-type "float[]"` with a scalar (or
`void`) input type raises a Python traceback ending in
`ValueError: array return type 'float[]' requires an array arg type (--arg-type 'T[]')`.

**Cause:** an array return only makes sense for a blockwise transform — array
in, array out of the same length. A scalar input paired with an array return
has no defined output length, so it is rejected.

**Fix:** for a blockwise transform, pass an array `--arg-type` too:
`just-makeit object x --arg-type "float[]" --return-type "float[]"` (or use the
`blockwise` preset). For a reduction (array in → one value), use a scalar
return type. To emit a variable-length block, use a `--multi-output` or
`--variable-output` method.

______________________________________________________________________

## `unknown return_type` when running `jm apply`

**Symptom:** `jm apply` refuses to generate:

```
error: unsupported type in just-makeit.toml:
module 'ber' function 'ber_lock_symbol': unknown return_type 'long'.
  Supported: void, bool, const char *, double, ...
  Did you mean 'int64_t'? ('long' has a platform-dependent width.)
```

The same check covers `result_fields` entries (gh-598), which report as

```
'det' method 'scan': result field 'idx' has unknown type 'wat_t'.
```

(`void` is absent from a field's supported list — every record field is a
value the binding has to convert.)

**Cause:** the manifest declares a `return_type` that is not one of jm's
registered types. Common causes are a natural C spelling whose width is
platform-dependent (`long`, `unsigned`, `ssize_t`), the *display* form of a
complex type (`float complex` — jm stores `float _Complex`), or a plain typo.

Before jm 0.33.14 this was accepted silently: the generated binding called
the C function, discarded its return value and emitted `Py_RETURN_NONE`. It
compiled cleanly and surfaced only at runtime, as a `None` where a number was
expected (gh-595). The check exists so that class of bug fails at generation
time instead.

**Fix:** use the fixed-width equivalent the error suggests (`int64_t` for
`long`, `uint32_t` for `unsigned`, `ptrdiff_t` for `ssize_t`), and change the
C function's own return type to match — the manifest and the C prototype have
to agree.

A `return_type` naming your own struct is **not** an error when the entry also
declares `result_fields`; that is the record shape, where the type names the
struct jm fills in rather than a value it converts:

```toml
[[module.ber.functions]]
name = "scan"
return_type = "ber_align_t"          # a user struct — fine, because:
result_fields = [{name = "lag", type = "int"}]
```

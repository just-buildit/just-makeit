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

# Windows: ships with Visual Studio Build Tools (see the FAQ's Windows entry)

# Or let the installer handle it:
just-makeit install-deps
```

Verify: `cmake --version` — must print 3.16 or higher.

______________________________________________________________________

## NumPy headers missing

**Symptom:** CMake's configure step stops with
`Could NOT find Python3 (missing: Python3_NumPy_INCLUDE_DIRS NumPy)`, or
`make` prints
`error: numpy is installed but its C headers are missing from <dir>`.

**Cause:** CMake asks the interpreter it was given (`Python3_EXECUTABLE`,
which `make` sets to the active `python3`) for `numpy.get_include()`. That
interpreter has no NumPy, or its headers are missing.

**Fix:** Build through `make` inside the activated venv. It reinstalls NumPy
when its headers are missing, then configures with that interpreter:

```sh
source .venv/bin/activate   # or the path printed by install.sh
make
```

If the venv is active and the error persists, confirm NumPy is installed:

```sh
python -c "import numpy; print(numpy.get_include())"
```

Configuring by hand, name the interpreter that has NumPy:

```sh
cmake -B build -DPython3_EXECUTABLE="$(command -v python)"
cmake --build build
```

`-DPython3_NumPy_INCLUDE_DIR=<dir>` names the header directory directly.

______________________________________________________________________

## C consumer fails to link (`--as-needed`)

**Symptom:** linking a C program against the installed library fails with
`undefined reference to 'my_project_<comp>_create'`, although
`pkg-config --libs my_project` names the library.

**Cause:** GNU ld on Debian/Ubuntu uses `--as-needed` by default. If the
library appears on the command line *before* the object files that reference
it, the linker silently drops it.

**Fix — pkg-config consumers:** split `--cflags` and `--libs`, with the source
file between them:

```sh
# WRONG — library before source
gcc $(pkg-config --cflags --libs my_project) consumer.c -o consumer

# CORRECT
gcc $(pkg-config --cflags my_project) consumer.c \
    $(pkg-config --libs my_project) -o consumer
```

The generated project's own CMake build and its Python extensions are
unaffected — only an external C consumer's command line is.

______________________________________________________________________

## `PKG_CONFIG_PATH` not set for custom prefix

**Symptom:** `pkg-config --cflags my_project` prints nothing or exits with
`Package my_project was not found in the pkg-config search path`.

**Cause:** You installed to a non-standard prefix (e.g. `$HOME/.local`) and
pkg-config doesn't search it by default.

**Fix:**

```sh
export PKG_CONFIG_PATH="$HOME/.local/lib/pkgconfig:$PKG_CONFIG_PATH"
pkg-config --modversion my_project   # should print the version
```

Add the `export` line to your shell profile to persist it.

______________________________________________________________________

## Windows: `make` not found, or the wrong compiler

**Symptom:** `'make' is not recognized as an internal or external command`,
or CMake picks `cl.exe`/MinGW and the build fails on `_Complex`.

**Fix:** install GNU make (`winget install ezwinports.make`) and run from a
Developer PowerShell with `clang-cl` on `PATH`. The generated `Makefile`
selects clang-cl and Ninja on Windows. See
[Does it work on Windows?](faq.md#does-it-work-on-windows); MinGW is no
longer supported.

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
re-renders it or rewrites a line you wrote; the one thing it does is ADD a
definition the manifest declares and the file lacks (gh-1294), which `jm status` lists as "STALE — yours". Apply regenerates the glue (`_ext.c`, `.pyi`,
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
`native/src/<mod>/<mod>_ext_<obj>.c` still has the old signature. `jm apply`
prints `warning ~: … binding no longer matches the manifest [...]` and
`jm status` lists the file under UNRECONCILED, but the old binding stays.

**Cause:** a module object's binding fragment is **shared**
([who owns each file](workflows/edit-lifecycle.md#who-owns-each-file)):
`jm apply` materializes files and methods that are *missing*, and reconciles
wiring — it does not re-render a binding that already exists. So the shape
frozen at the method's first `apply` is the one you keep.

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

Your `_core.c` algorithm is untouched — only the binding is rebuilt. To stop
maintaining the fragment by hand, make it jm's, so every `apply` re-renders
it: `jm adopt <obj>` (`jm adopt --check` previews; it refuses a unit that
would lose code until you `--accept` it by name). A standalone object's
`<obj>_ext.c` is already jm's, and `apply` re-renders it.

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

On the CLI, `--out-param w:float[]` declares it, on `jm function` and on
`jm method` alike:

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

A method's output-side types are checked the same way (gh-1977), each against
what its command-line flag accepts: `out_type` must be an array element type
(`--out-type`), each `multi_output` entry a registered scalar
(`--multi-output`), and `extra_args` follows `params`:

```
'o' method 'm': out_type 'void' has no numpy equivalent.
'o' method 'm': multi_output type 'wat_t' is not supported.
```

**Cause:** the manifest declares a `return_type` that is not one of jm's
registered types. Common causes are a natural C spelling whose width is
platform-dependent (`long`, `unsigned`, `ssize_t`), the *display* form of a
complex type (the `<complex.h>` macro `complex`, where jm stores `_Complex`),
or a plain typo.

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

______________________________________________________________________

## `error:` with no stack trace, or a traceback

**Symptom:** a command stops with one line, for example

```
error: objects/gen.toml: 'gen' already exists. Run `jm remove object gen` first, or rename the object in the fragment.
```

**Cause:** jm refused the manifest or the command on purpose, and the
message is the whole report: the file or key, what is wrong, what to do. It
exits 1. Set `JM_DEBUG=1` to see where it was raised:

```sh
JM_DEBUG=1 jm apply
```

A Python **traceback** without `JM_DEBUG` is either a bug in jm or a
refusal not yet marked as one (gh-1783 tracks those). Report it either way,
with the command and the manifest that produced it.

# Commands

## `just-makeit new <proj> [--object name] [--state name:type[:default] ...]`

Create a new project. Optionally scaffold a first object in the same step.

```sh
just-makeit new my_project
just-makeit new my_project --object engine
just-makeit new my_project --object engine --state rate:double:1.0
just-makeit new my_project --object engine --state rate:double --state order:int:4
just-makeit new my_project --object gain --arg-type float --return-type float --state gain:float:1.0
just-makeit new my_filters --module filter
just-makeit new my_dsp --module osc --module env
```

`new` writes a `just-makeit.toml` that records the project name, version, and
any objects — the source of truth for all subsequent commands.

**Arguments**

| Argument                      | Description                                                                          |
| ----------------------------- | ------------------------------------------------------------------------------------ |
| `project`                     | Project name in `snake_case`. Used as the Python package name and distribution name. |
| `--object name`               | Scaffold a first standalone object immediately (optional).                           |
| `--module name`               | Scaffold an empty extension module immediately. Repeatable; mutually exclusive with `--object`. |
| `--state name:type[:default]` | Declare a state variable for the object. Repeatable.                                 |
| `--arg-type TYPE`             | C type for `step()` input `x`. Defaults to `float _Complex`.                        |
| `--return-type TYPE`          | C type for `step()` return value. Defaults to `--arg-type`.                         |
| `--pure`                      | Generate a stateless object. See [Stateful vs pure](pure.md).                       |

______________________________________________________________________

## `just-makeit module <name>`

Scaffold a new Python extension module — a subpackage `.so` that groups
multiple types added via `just-makeit object`.  Must be run from the project root.

```sh
just-makeit module filter
just-makeit module osc
```

Creates:

| File | Purpose |
|------|---------|
| `native/src/<name>/<name>_ext.c` | C extension (empty — no types yet) |
| `native/src/<name>/CMakeLists.txt` | Python module target |
| `src/<pkg>/<name>/__init__.py` | Subpackage init (empty exports) |

Appends `add_subdirectory(native/src/<name>)` to the root `CMakeLists.txt`
and records `[module.<name>]` with an empty `objects` list in `just-makeit.toml`.

Types are added with `just-makeit object`.

______________________________________________________________________

## `just-makeit object <name> [--module <name>] [--state name:type[:default] ...] [--pure] [--arg-type TYPE] [--return-type TYPE]`

Add a Python type to the project.  Must be run from the project root.

**Without `--module` — standalone object (own `.so`):**

```sh
just-makeit object engine --state rate:double:1.0
just-makeit object ema --arg-type float --return-type float --state alpha:double:0.1 --state prev:float:0.0
just-makeit object normalize --pure --param scale:double:1.0
```

Creates the full standalone set of files, updates the top-level `CMakeLists.txt`,
and splices the import + `__all__` entry into `src/<pkg>/__init__.py`.

**With `--module` — grouped into a module subpackage `.so`:**

```sh
just-makeit object fir --module filter --state "coeffs:float[16]" --state "delay:float _Complex[16]" --state "gain:float:1.0"
just-makeit object biquad --module filter --state "b0:double:1.0" --state "a1:double:0.0" --state "w1:double:0.0"
```

**Per-object files created** (same for both modes):

| File | Purpose |
|------|---------|
| `native/inc/<obj>/<obj>_core.h` | Header: struct, inline `_step`, getters/setters |
| `native/src/<obj>/<obj>_core.c` | Source: create/destroy/reset/steps |
| `native/src/<obj>/CMakeLists.txt` | OBJECT library + C test + bench |
| `native/tests/test_<obj>_core.c` | C test with `CHECK` macro counter |
| `native/benchmarks/bench_<obj>_core.c` | C benchmark |

**Additional files for standalone objects** (no `--module`):

| File | Purpose |
|------|---------|
| `native/src/<obj>/<obj>_ext.c` | Python C extension (own `.so`) |
| `src/<pkg>/<obj>.pyi` | Type stub |
| `src/<pkg>/tests/test_<obj>.py` | pytest suite |

**Module files regenerated** after each `just-makeit object --module`:

| File | What changes |
|------|-------------|
| `native/src/<module>/<module>_ext.c` | New type block added; `PyMODINIT_FUNC` updated |
| `native/src/<module>/CMakeLists.txt` | New `<obj>_core` added to link list |
| `src/<pkg>/<module>/__init__.py` | New type added to import and `__all__` |

The module `_ext.c` is always fully regenerated from the complete object list
— never patched — so adding a third type never disturbs the first two.

**Arguments**

| Argument | Description |
|----------|-------------|
| `name` | Object name in `snake_case`. Becomes the C prefix and Python class name (title-cased). |
| `--module name` | Target module. Without this flag the object is standalone (own `.so`). |
| `--state name:type[:default]` | Declare a state variable. Repeatable. |
| `--arg-type TYPE` | C type for `step()` input. Defaults to `float _Complex`. |
| `--return-type TYPE` | C type for `step()` return value. Defaults to `--arg-type`. |
| `--pure` | Generate a stateless object. |

See [State Variable Types](types.md) for supported types, defaults, and C/Python mappings.

Each `--state name:type[:default]` generates:

- A field in the C state struct
- A constructor parameter with the declared default
- `get_name()` and `set_name()` methods in both C and Python
- Getter/setter tests in both CTest and pytest
- Reset behaviour that restores the declared default

**Naming rules**

- Lowercase letters, digits, and underscores only.
- Must not start with a digit.
- Examples: `engine`, `parser`, `rate_limiter`

The Python class name is derived automatically:

| Object name        | Python class     |
| ------------------ | ---------------- |
| `engine`           | `Engine`         |
| `rate_limiter`     | `RateLimiter`    |
| `half_band_filter` | `HalfBandFilter` |

______________________________________________________________________

## `just-makeit add --state name:type[:default] [...] [--object name]`

Add one or more state variables to an existing standalone object. Must be run
from the project root.

```sh
just-makeit add --state order:int:4
just-makeit add --state threshold:double:0.5 --state window:int:64
just-makeit add --object parser --state depth:int:8
```

When the project has a single standalone object `--object` may be omitted.

`add` regenerates the six state-sensitive files from the merged state list:

- `native/inc/<obj>/<obj>_core.h`
- `native/src/<obj>/<obj>_core.c`
- `native/src/<obj>/<obj>_ext.c`
- `native/tests/test_<obj>_core.c`
- `src/<project>/<obj>.pyi`
- `src/<project>/tests/test_<obj>.py`

All six files are backed up before regeneration.  If any write fails, they
are restored and `just-makeit.toml` is left unchanged.

**Constraints**

- Each new variable name must be unique within the object's state list.
- Requires a `just-makeit.toml` — run `just-makeit new` first.

______________________________________________________________________

## `just-makeit perf`

Upgrade an existing project to use performance annotations without
overwriting any user code.  Must be run from the project root.

```sh
just-makeit perf
```

Writes `native/inc/jm_perf.h`, adds `#include "jm_perf.h"` to each object
header, and replaces `static inline` with `JM_FORCEINLINE JM_HOT` on `step()`.
Records `perf = true` in `just-makeit.toml` so future `object`/`add` commands
inherit it.  Safe to run on a project with a filled-in `step()`.  Idempotent.

See [Performance annotations](perf.md) for the full macro reference and
`JM_DEFINE_STEPS` documentation.

______________________________________________________________________

## `just-makeit config [key value]`

Show or edit the project configuration stored in `just-makeit.toml`.
Must be run from the project root.

```sh
just-makeit config                 # print current config
just-makeit config version 0.2.0  # update version
```

**Example output**

```
project:  my_project
version:  0.1.0

engine:
  rate:  double = 1.0
  order: int    = 4

parser:
  depth:  int = 8
  strict: int = 1
```

**Supported keys**

| Key       | Description                                          |
| --------- | ---------------------------------------------------- |
| `version` | Project version string stored in `just-makeit.toml`. |

______________________________________________________________________

## `just-makeit build [dir]`

Configure the CMake project (if not already done), build the C extensions, and
package a wheel via just-buildit.

```sh
just-makeit build           # wheel → dist/
just-makeit build wheels/   # wheel → wheels/
```

Must be run from a project directory containing `pyproject.toml`.

______________________________________________________________________

## `just-makeit test`

Build (if needed), then run CTest and pytest.

```sh
just-makeit test
```

- CTest runs the C tests in each object's `tests/` directory.
- pytest runs the Python tests in `src/`.

______________________________________________________________________

## `just-makeit dry-run`

Show what would be compiled and packaged without running any build steps.

```sh
just-makeit dry-run
```

Output includes the list of C source files and the full cmake configure
command that `just-makeit build` would invoke.

# Commands

## `just-makeit new <proj> [--component name] [--state name:type[:default] ...]`

Create a new project. Optionally scaffold a first component in the same step.

```sh
just-makeit new my_project
just-makeit new my_project --component engine
just-makeit new my_project --component engine --state rate:double:1.0
just-makeit new my_project --component engine --state rate:double --state order:int:4
```

`new` writes a `just-makeit.toml` that records the project name, version, and
any components — the source of truth for all subsequent commands.

**Arguments**

| Argument                      | Description                                                                          |
| ----------------------------- | ------------------------------------------------------------------------------------ |
| `project`                     | Project name in `snake_case`. Used as the Python package name and distribution name. |
| `--component name`            | Scaffold a first component immediately (optional).                                   |
| `--state name:type[:default]` | Declare a state variable for the component. Repeatable.                              |
| `--pure`                      | Generate a stateless component. See [Stateful vs pure](pure.md).                    |

______________________________________________________________________

## `just-makeit init <component> [--state name:type[:default] ...]`

Add a new component (C extension) to an existing project. Must be run from the
project root (where `just-makeit.toml` lives).

```sh
just-makeit init engine --state rate:double:1.0
just-makeit init parser --state depth:int:8 --state strict:int:1
```

Creates the component directory, updates the top-level `CMakeLists.txt` with
`add_subdirectory`, registers the component in `just-makeit.toml`, and adds
the Python type stub and test file.

**Arguments**

| Argument                      | Description                                                                                   |
| ----------------------------- | --------------------------------------------------------------------------------------------- |
| `component`                   | Component name in `snake_case`. Becomes the C prefix, Python module name, and directory name. |
| `--state name:type[:default]` | Declare a state variable. Repeatable. Defaults to `gain:double:0.0` if omitted entirely.      |
| `--pure`                      | Generate a stateless component. See [Stateful vs pure](pure.md).                    |

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

| Component name     | Python class     |
| ------------------ | ---------------- |
| `engine`           | `Engine`         |
| `rate_limiter`     | `RateLimiter`    |
| `half_band_filter` | `HalfBandFilter` |

______________________________________________________________________

## `just-makeit add --state name:type[:default] [...] [--component name]`

Add one or more state variables to an existing component. Must be run from the
project root.

```sh
just-makeit add --state order:int:4
just-makeit add --state threshold:double:0.5 --state window:int:64
just-makeit add --component parser --state depth:int:8
```

When the project has a single component `--component` may be omitted.

`add` regenerates the six state-sensitive files from the merged state list:

- `<component>/inc/<component>/<component>_core.h`
- `<component>/src/<component>_core.c`
- `<component>/src/<component>_ext.c`
- `<component>/tests/test_<component>_core.c`
- `src/<project>/<component>.pyi`
- `src/<project>/tests/test_<component>.py`

All six files are backed up before regeneration.  If any write fails, they
are restored and `just-makeit.toml` is left unchanged.

**Constraints**

- Each new variable name must be unique within the component's state list.
- Requires a `just-makeit.toml` — run `just-makeit new` first.

______________________________________________________________________

## `just-makeit perf`

Upgrade an existing project to use performance annotations without
overwriting any user code.  Must be run from the project root.

```sh
just-makeit perf
```

Writes `native/inc/jm_perf.h`, adds `#include "jm_perf.h"` to each component
header, and replaces `static inline` with `JM_FORCEINLINE JM_HOT` on `step()`.
Records `perf = true` in `just-makeit.toml` so future `init`/`add` commands
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

- CTest runs the C tests in each component's `tests/` directory.
- pytest runs the Python tests in `src/`.

______________________________________________________________________

## `just-makeit dry-run`

Show what would be compiled and packaged without running any build steps.

```sh
just-makeit dry-run
```

Output includes the list of C source files and the full cmake configure
command that `just-makeit build` would invoke.

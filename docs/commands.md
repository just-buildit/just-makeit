# Commands

## `just-makeit init <name> [dir] [--state name:type[:default] ...]`

Create a new C extension project in a new directory.

```sh
just-makeit init my_filter
just-makeit init my_filter --state gain:double:1.0
just-makeit init my_bpf --state center:double:1000.0 --state bw:double:200.0 --state order:int:4
just-makeit init my_filter ~/dev/my_filter --state gain:double
```

**Arguments**

| Argument | Description |
|---|---|
| `name` | Component name in `snake_case`. Used as the C prefix, Python module name, and directory name. |
| `dir` | Destination directory for the new project (default: `./<name>`). |
| `--state name:type[:default]` | Declare a state variable. Repeatable. Defaults to `gain:double:0.0` if omitted entirely. |

**State types**

| Type | C type | Python type | Zero default |
|---|---|---|---|
| `double` | `double` | `float` | `0.0` |
| `float` | `float` | `float` | `0.0f` |
| `int` | `int` | `int` | `0` |

The `:default` suffix is optional — the type's zero value is used if omitted.
Both `_reset()` and Python `__init__()` use the declared default, so
`MyFilter()` with no arguments is always valid.

Each `--state name:type[:default]` generates:
- A field in the C state struct
- A constructor parameter with the declared default
- `get_name()` and `set_name()` methods in both C and Python
- Getter/setter tests in both CTest and pytest
- Reset behaviour that restores the declared default

`init` also writes a `just-makeit.toml` that records the component name,
version, and full state var list — used by `add` and `config`.

**Naming rules**

- Lowercase letters, digits, and underscores only.
- Must not start with a digit.
- Examples: `gain`, `my_filter`, `half_band_decim`

The Python class name is derived automatically:

| Component name | Python class |
|---|---|
| `gain` | `Gain` |
| `my_filter` | `MyFilter` |
| `half_band_decim` | `HalfBandDecim` |

---

## `just-makeit add --state name:type[:default] [...]`

Add one or more state variables to an existing project.  Must be run from the
project root (where `just-makeit.toml` lives).

```sh
just-makeit add --state order:int:4
just-makeit add --state bandwidth:double:200.0 --state poles:int:2
```

`add` regenerates the six state-sensitive files from the merged state list:

- `native/inc/<comp>/<comp>_core.h`
- `native/src/<comp>/<comp>_core.c`
- `native/src/<comp>/<comp>_ext.c`
- `native/tests/test_<comp>_core.c`
- `src/<comp>/<comp>.pyi`
- `src/<comp>/tests/test_<comp>.py`

All six files are backed up before regeneration.  If any write fails, they
are restored to their pre-`add` state and `just-makeit.toml` is left unchanged.

**Constraints**

- Each new variable name must be unique across the full state list.
- Requires a `just-makeit.toml` — run `just-makeit init` first.

---

## `just-makeit config [key value]`

Show or edit the project configuration stored in `just-makeit.toml`.
Must be run from the project root.

```sh
just-makeit config                 # print current config
just-makeit config version 0.2.0  # update version
```

**Example output**

```
component: my_filter
version:   0.1.0
state:
  gain: double = 1.0
  order: int = 4
```

**Supported keys**

| Key | Description |
|---|---|
| `version` | Project version string stored in `just-makeit.toml`. |

---

## `just-makeit build [dir]`

Configure the CMake project (if not already done), build the C extension, and
package a wheel via just-buildit.

```sh
just-makeit build           # wheel → dist/
just-makeit build wheels/   # wheel → wheels/
```

Must be run from a project directory containing `pyproject.toml`.

---

## `just-makeit test`

Build (if needed), then run CTest and pytest.

```sh
just-makeit test
```

- CTest runs the C tests in `native/tests/`.
- pytest runs the Python tests in `src/`.

---

## `just-makeit dry-run`

Show what would be compiled and packaged without running any build steps.

```sh
just-makeit dry-run
```

Output includes the list of C source files and the full cmake configure
command that `just-makeit build` would invoke.

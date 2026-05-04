# Commands

## `just-makeit init <name> [dir] [--state name:type ...]`

Create a new C extension project in a new directory.

```sh
just-makeit init my_filter --state gain:double
just-makeit init my_bpf --state center:double --state bw:double --state order:int
just-makeit init my_filter ~/dev/my_filter --state gain:double
```

**Arguments**

| Argument | Description |
|---|---|
| `name` | Component name in `snake_case`. Used as the C prefix, Python module name, and directory name. |
| `dir` | Destination directory for the new project (default: `./<name>`). |
| `--state name:type` | Declare a state variable. Repeatable. Defaults to `gain:double` if omitted. |

**State types**

| Type | C type | Python type |
|---|---|---|
| `double` | `double` | `float` |
| `float` | `float` | `float` |
| `int` | `int` | `int` |

Each `--state name:type` generates:
- A field in the C state struct
- A constructor parameter
- `get_name()` and `set_name()` methods in both C and Python
- Getter/setter tests in both CTest and pytest

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

# App command

______________________________________________________________________

## `just-makeit app [--target c|console|pep723] [--object name] [--name name]`

Scaffold a shippable standalone application from an existing component.
Run this after `just-makeit object` to turn a C extension into something
you can hand to an end user.

```sh
# C executable — reads from stdin, calls your component per sample
jm app --target c --object engine --name dsp_tool

# Python console script — argparse boilerplate wired to the Python bindings
jm app --target console --object engine --name dsp_tool

# PEP 723 inline script — single .py file, no install required
jm app --target pep723 --object engine --name dsp_tool
```

All three targets write an `[app]` section to `just-makeit.toml` so
`jm apply` can regenerate the scaffold later.

**Arguments**

| Argument                      | Description                                                                                                   |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `--target c\|console\|pep723` | Output target. Default: `c`.                                                                                  |
| `--object name`               | Component to scaffold from. Must already exist in `just-makeit.toml`. Defaults to the first listed component. |
| `--name name`                 | Name for the generated app/script. Defaults to the project name.                                              |

______________________________________________________________________

## Targets

### `--target c`

Generates `native/src/app/<name>.c` — a `main()` that calls your
component's full lifecycle: `create` → process loop (`step`) → `destroy`.
Constructor arguments default to the values declared in `--state`.

Also appends to `CMakeLists.txt`:

```cmake
add_executable(<name> native/src/app/<name>.c)
target_link_libraries(<name> PRIVATE <component>_core)
install(TARGETS <name> DESTINATION bin)
```

If `CMakeLists.txt` does not exist, the commands are printed for manual
addition instead. Running `jm app --target c` a second time replaces the
existing block (idempotent).

**After scaffolding:** open `native/src/app/<name>.c` and implement the
I/O loop marked `/* <<IMPLEMENT>> */`. Build with:

```sh
make && ./build/<name>
```

______________________________________________________________________

### `--target console`

Generates `src/<pkg>/cli.py` — a Python script using `argparse` with one
`--<param>` flag for every constructor scalar, each defaulting to the value
declared in `--state`. The processing loop and output formatting are left
as `<<IMPLEMENT>>` stubs.

Also updates `[project.scripts]` in `pyproject.toml` so the script is
installed as `<name>` when the package is installed:

```toml
[project.scripts]
dsp_tool = "my_project.cli:main"
```

If `tomlkit` is not installed or `pyproject.toml` does not exist, the
required snippet is printed for manual addition.

**After scaffolding:** implement the processing loop in `src/<pkg>/cli.py`,
then install and run:

```sh
pip install -e .
<name> --help
```

______________________________________________________________________

### `--target pep723`

Generates `<name>.py` in the project root — a
[PEP 723](https://peps.python.org/pep-0723/) inline-metadata script with
an embedded `# /// script` dependency block referencing your package. It
can be run without a full install:

```sh
uv run <name>.py --help
```

The script contains the same `argparse` boilerplate as `--target console`
and is fully self-contained. Distribute the single `.py` file to users who
have `uv` installed; they do not need to install your package separately as
long as it is available on PyPI.

**Note:** the `# /// script` block names your package as a dependency.
Until the package is published to PyPI (or a local index), `uv run` will
fail. Use `--target console` during development and switch to `--target pep723` when the package is public.

______________________________________________________________________

## TOML record

Every `jm app` run records the scaffolded app in `just-makeit.toml`:

```toml
[app]
target  = "console"
name    = "dsp_tool"
object  = "engine"
```

`jm apply` reads this section and regenerates the app scaffold alongside
all other project files.

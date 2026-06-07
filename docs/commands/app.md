# App command

______________________________________________________________________

## `just-makeit app [--target c|console|pep723] [--object name | --function name] [--name name] [--flag ...] [--command ...]`

Scaffold a shippable, **runnable** application from an existing component —
the "one C core, three faces" pattern. Run it after `just-makeit object` (or
`just-makeit function`) to turn a C extension into a CLI you can hand to a
user.

```sh
# C executable
jm app --target c --object engine --name dsp_tool

# Python console script (installed via [project.scripts])
jm app --target console --object engine --name dsp_tool

# PEP 723 inline script (run with `uv run`, no install)
jm app --target pep723 --object engine --name dsp_tool
```

As of **0.15.0** the generated app is *complete*, not a stub: each target
emits a real argument parser **and** a working read → process → write loop,
generated from the object model. The C `strtof`/`argv` parser and the Python
`argparse` setup are produced from the same spec, so the C binary and the
Python CLI accept the same flags and behave the same way. There is nothing to
hand-edit before it runs.

Every run records an `[app]` section (and any `[[app.flags]]` /
`[[app.commands]]`) in `just-makeit.toml`, so `jm apply` recreates the scaffold.

**Arguments**

| Argument                            | Description                                                                             |
| ----------------------------------- | --------------------------------------------------------------------------------------- |
| `--target c\|console\|pep723`       | Output target (repeatable intent via re-runs). Default: `c`.                            |
| `--object name`                     | Component to wrap. Defaults to the first component.                                     |
| `--function name`                   | Wrap a module-level function instead of an object (see *Function CLIs*).                |
| `--name name`                       | Name of the generated app/script. Defaults to the project name.                         |
| `--flag name:type[:default[:help]]` | Extra control flag, added to both parsers and persisted as `[[app.flags]]`. Repeatable. |
| `--command name[:help]`             | Declare a subcommand (multi-command CLI). Repeatable. See *Subcommands*.                |

______________________________________________________________________

## Object shapes

`jm app` reads the object's `step`/`steps` signature and generates the I/O
loop that fits it — no flag needed:

| Shape         | Signature                           | Generated I/O                                                |
| ------------- | ----------------------------------- | ------------------------------------------------------------ |
| **scalar**    | `step(x) -> y`                      | read one sample → `step()` → write one, in a loop            |
| **blockwise** | `T[] -> U[]` (`--preset blockwise`) | read a block → `steps(in, n, out)` → write the block         |
| **consumer**  | `T -> void`                         | read → `step()`; no output side                              |
| **generator** | `void -> T`                         | synthetic `--count N` drives `step()` → write; no input side |

Constructor state vars become `--<name>` flags wired into `create()`, each
defaulting to its `--state` default. Extra `--flag` controls are appended to
both the C and Python parsers.

______________________________________________________________________

## Output axes (complex-float streams)

When the output element type is **complex float32** (a `generator` or
`blockwise` shape returning `float _Complex`), `jm app` adds a set of built-in
output flags — no declaration needed. They are generated **byte-identically
across all three faces** (the C face converts inline; the Python faces use
numpy), so a capture is reproducible regardless of which face produced it.

| Flag            | Values                            | Effect                                                                                                                                     |
| --------------- | --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `--sample_type` | `cf32` `cf64` `ci32` `ci16` `ci8` | wire type, converted on write (full-scale ±1.0 for the integer types)                                                                      |
| `--file_type`   | `raw` `csv`                       | `raw` interleaved I/Q (default) or text `I,Q` lines (`%0.9f` cf32, `%0.17g` cf64, `%d` integer)                                            |
| `--endian`      | `le` `be`                         | byte order; `be` reverses each element (raw only — csv is text)                                                                            |
| `--record FILE` | path                              | write a JSON record of the fully-resolved run (every flag after defaulting; choice flags as their chosen string) for reproducible captures |

`--sample_type` shipped in 0.16.0; `--file_type` / `--endian` / `--record` in
0.17.0.

> **Richer containers stay application-side.** Formats that need context a
> generic generator can't know — sample rate (BLUE `xdelta`), per-segment
> annotations (SigMF), or a transport (zmq) — are *not* generated. Provide them
> in a hand-written tool over your C cores (see doppler's `wfmgen` composer,
> which adds BLUE / SigMF / `zmq://` alongside the generated `wavegen`).

______________________________________________________________________

## Targets

### `--target c`

Generates `native/src/app/<name>.c` — a `main()` with an `argv` parser
(`strtof`/`strtol` per flag type) and the shape-appropriate
`create → loop → destroy`. Appends an `add_executable` / `target_link_libraries`
/ `install` block to `CMakeLists.txt` (printed for manual addition if there is
no `CMakeLists.txt`). Re-running replaces the block (idempotent).

```sh
make && ./build/<name> --help
```

### `--target console`

Generates `src/<pkg>/<name>_cli.py` — an `argparse` CLI over the Python
bindings, one `--<param>` per constructor scalar plus any `--flag`s, with the
matching process loop. Adds `<name>` to `[project.scripts]` in `pyproject.toml`
(snippet printed if `tomlkit`/`pyproject.toml` is unavailable).

```sh
pip install -e . && <name> --help
```

### `--target pep723`

Generates `<name>.py` in the project root — a self-contained
[PEP 723](https://peps.python.org/pep-0723/) script with an inline
`# /// script` dependency block, the same parser/loop as `console`.

```sh
uv run <name>.py --help
```

The `# /// script` block names your package as a dependency, so `uv run`
needs it published (or on a local index). Use `--target console` during
development.

______________________________________________________________________

## Function CLIs (`--function`)

`jm app --function <name> [--module m]` generates a CLI over a **module-level
function** instead of an object: each scalar parameter becomes a flag, the
function is called once, and the result is printed.

```sh
jm app --target console --function kaiser_beta --module resample --name kaiser
# kaiser --atten 60   ->  prints the computed beta
```

______________________________________________________________________

## Subcommands (`--command` / `[[app.commands]]`)

Pass `--command name[:help]` (repeatable) to scaffold a multi-command CLI:

```sh
jm app --target c --object engine --name dsp_tool \
    --command encode:"encode a stream" --command decode:"decode a stream"
```

The C target generates an `argv[1]` dispatch with a per-command flag-parsing
handler; the Python targets generate an `argparse` subparsers tree. Each
command body is an `<<IMPLEMENT>>` stub — the dispatch, parsing, and help are
generated; you fill in the per-command logic. Commands persist as
`[[app.commands]]` and are recreated by `jm apply`.

______________________________________________________________________

## TOML record

```toml
[app]
target  = "console"
name    = "dsp_tool"
object  = "engine"     # or: function = "kaiser_beta"

[[app.flags]]
name = "gain"
type = "float"
default = "1.0"
help = "output gain"

[[app.commands]]
name = "encode"
help = "encode a stream"
```

`jm apply` reads `[app]` (+ `[[app.flags]]` / `[[app.commands]]`) and
materializes the app scaffold if its file is missing. Re-running `jm app`
overwrites the scaffold directly.

See the bundled `three_face` and `app_shapes` examples
(`jm example three_face`, `jm example app_shapes`) for end-to-end runs.

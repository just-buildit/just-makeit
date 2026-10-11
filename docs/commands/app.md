# App command

______________________________________________________________________

## `just-makeit app`

```text
just-makeit app
    [--target c|console|pep723]
    [--object name | --function name]
    [--module name]
    [--name name]
    [--flag name:type[:default[:help]] ...]
    [--command name[:help] ...]
    [--argc-argv]
```

Scaffold a shippable, **runnable** application from an existing component —
the "one C core, three faces" pattern. Run it after `just-makeit object` (or
`just-makeit function`) to turn a C extension into a CLI you can hand to a
user.

```sh
# C executable
jm app --target c --object engine --name dsp_tool

# Python console script (installed via [project.scripts])
jm app --target console --object engine --name dsp_tool_py

# PEP 723 inline script (run with `uv run`, no install)
jm app --target pep723 --object engine --name dsp_tool_script
```

As of **0.15.0** the generated app is *complete*, not a stub, for the four
object shapes listed below: each target emits a real argument parser **and**
a working read → process → write loop, generated from the object model. The
C `strtof`/`argv` parser and the Python `argparse` setup are produced from
the same spec, so the C binary and the Python CLI accept the same flags and
behave the same way. There is nothing to hand-edit before it runs.

Every run **appends** an `[[app]]` row to `just-makeit.toml` — the app's
target, its source, and any `[[app.flags]]` / `[[app.commands]]` — so a
project holds as many apps as you declare, and `jm apply` recreates each one
(gh-2074). An app is keyed by its `--name`, which is unique in the project:

- a name already taken is refused, naming `jm remove app <name>`;
- so is a second app whose *default* name is taken (the project's name, or
    the function's), naming `--name` — which is why the three faces above are
    named apart;
- and so is a second console app over the same package, whose `cli.py` it
    would overwrite (see *Targets* below).

[`jm remove app <name>`](#removing-an-app) takes one out.

**Arguments**

| Argument                            | Description                                                                                                                                                                                           |
| ----------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--target c\|console\|pep723`       | Output target. Default: `c`. Short form: `-t`. One app per run; run `jm app` again, under another `--name`, for another face.                                                                         |
| `--object name`                     | Component to wrap. Defaults to the first component.                                                                                                                                                   |
| `--function name`                   | Wrap a module-level function instead of an object (see *Function CLIs*).                                                                                                                              |
| `--module name`                     | Module the `--object` or `--function` lives in; it decides the import path and where `cli.py` goes.                                                                                                   |
| `--name name`                       | Name of the generated app/script, unique in the project. Defaults to the project name (the function name with `--function`).                                                                          |
| `--flag name:type[:default[:help]]` | Extra control flag, added to both parsers and persisted as `[[app.flags]]`. Repeatable.                                                                                                               |
| `--command name[:help]`             | Declare a subcommand (multi-command CLI). Repeatable. See *Subcommands*.                                                                                                                              |
| `--argc-argv`                       | For an object shape `jm app` doesn't generate a full loop for (e.g. a `--no-step` reader): emit an `argv`-parsing skeleton in the C target's stub instead of a plain `(void)argc; (void)argv;` no-op. |

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

An object outside these four shapes — a `--no-step` object such as a
[reader](../templates/reader.md), or one with an unsupported `--arg-type`/
`--return-type` combination — still scaffolds, but `jm app` has no I/O loop
to generate for it: the C target gets an `<<IMPLEMENT: parse argv>>` stub
(or `--argc-argv`'s skeleton) and the Python targets get an
`<<IMPLEMENT: open input/output, call obj.step(), write>>` stub. `--function`
apps (see below) are unaffected — they always call the function once and
print the result.

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
`create → loop → destroy`. jm's App block in `CMakeLists.txt` holds an
`add_executable` / `target_link_libraries` / `install` group for every C app
the manifest declares, and is rewritten from it on each run (idempotent;
printed for manual addition if there is no `CMakeLists.txt`).

```sh
make && ./build/<name> --help
```

### `--target console`

Generates `src/<pkg>/cli.py` (or `cli.py` in the module's package directory
for a `--function`/`--object` inside a module: `src/<pkg>/<module>/`, or the
module's `package` when it declares one) — an `argparse` CLI over the Python
bindings, one `--<param>` per constructor scalar plus any `--flag`s, with the
matching process loop. Adds `<name>` to `[project.scripts]` in `pyproject.toml`
(snippet printed if `tomlkit`/`pyproject.toml` is unavailable). The module is
the package's `cli.py`, not a file named after the app, so one package holds
one console app: a second over the same package is refused, naming the first
app's removal.

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
command body is an `<<IMPLEMENT>>` marker. The file is regenerated by every
`jm app` and `jm apply`, command bodies included, so implement each command in
a component (`jm method` / `jm function`) and call it from the body rather
than writing the logic into the generated file. Commands persist as
`[[app.commands]]`.

______________________________________________________________________

## TOML record

One `[[app]]` row per app. TOML attaches each `[[app.flags]]` and
`[[app.commands]]` row to the `[[app]]` row above it:

```toml
[[app]]
target  = "c"
name    = "dsp_tool"
object  = "engine"     # or: function = "kaiser_beta"

[[app.flags]]
name = "gain"
type = "float"
default = "1.0"
help = "output gain"

[[app]]
target  = "console"
name    = "dsp_cmds"

[[app.commands]]
name = "encode"
help = "encode a stream"
```

`jm apply` reads every `[[app]]` row (with its `[[app.flags]]` /
`[[app.commands]]`) and regenerates each app from it on **every** run, as
`jm app` does. The app source is jm's: edits to it are discarded. Put custom
logic in a component (`jm method`) and call it from the generated `main()`.

Schema 8 and earlier held one app, as a single `[app]` table. This jm reads
the `[[app]]` spelling alone and refuses the old one, naming `jm upgrade`,
which rewrites it in place as a one-row `[[app]]` — see
[Upgrading](../upgrading.md#several-apps-gh-2074).

______________________________________________________________________

## Removing an app

```sh
jm remove app dsp_tool [--force]
```

Removes the `[[app]]` row named `dsp_tool` and the app's file —
`native/src/app/<name>.c`, the package's `cli.py`, or `<name>.py` — with its
wiring: the C app's lines in the App block of `CMakeLists.txt`, the console
app's `[project.scripts]` entry. A file you edited is kept, with a note to
delete it by hand, the way `jm remove method` leaves an authored body in
`_core.c`.

An object, module or function an app is built from cannot be removed while
the app is there: `jm remove` refuses, naming each app and
`jm remove app <name>`, and changes nothing (gh-2075).

See the bundled `three_face` and `app_shapes` examples
(`jm example three_face`, `jm example app_shapes`) for end-to-end runs.

# Build & tooling commands

______________________________________________________________________

## `just-makeit build [dir]`

Build the C extensions and package a wheel.

```sh
just-makeit build           # wheel → dist/
just-makeit build wheels/   # wheel → wheels/
```

Configures CMake (if not already done), builds the C extensions, then runs
`pip wheel` via [just-buildit](https://github.com/just-buildit/just-buildit).
Must be run from a project directory containing `pyproject.toml`.

______________________________________________________________________

## `just-makeit test`

Build (if needed), then run all tests.

```sh
just-makeit test
```

- CTest runs the C tests in each object's `tests/` directory.
- pytest (or `unittest`, depending on how the project was scaffolded) runs
    the Python tests in `src/`.

______________________________________________________________________

## `just-makeit dry-run`

Show what would be compiled and packaged without running any build steps.

```sh
just-makeit dry-run
```

Output includes the list of C source files and the full `cmake` configure
command that `just-makeit build` would invoke.

______________________________________________________________________

## `just-makeit bench [comp …]`

Build the project, run the C and Python benchmarks, and save a dated snapshot
under `benchmarks/history/` so performance history lives in git.

```sh
just-makeit bench              # all components, both sides, saves a snapshot
just-makeit bench fir biquad   # only these components
```

Each run rebuilds via `cmake`, executes every `bench_<comp>_core` C binary and
the `pytest-benchmark` suite under `src/`, prints a stats table per side with a
Δ column against the previous snapshot, and writes two immutable files —
`<tag>.json` (Python) and `<tag>-c.json` (C), where `<tag>` is a UTC timestamp.
Commit them to keep the history.

**Gate mode.** `--check` compares against a baseline instead of saving and
exits non-zero on a regression, so CI can fail a change that slows a kernel
down:

```sh
just-makeit bench --check --threshold 0.10   # fail if anything is >10% slower
```

**Arguments**

| Argument                     | Description                                                         |
| ---------------------------- | ------------------------------------------------------------------- |
| `comp …`                     | Restrict to the named components (default: all).                    |
| `--tag TAG`                  | Snapshot tag (default: a UTC timestamp).                            |
| `--c-only` / `--python-only` | Run only one benchmark side.                                        |
| `--check`                    | Compare against a baseline and exit 1 on regression; saves nothing. |
| `--threshold N`              | Fractional slowdown that fails `--check` (default `0.10` = 10%).    |
| `--baseline TAG`             | Baseline snapshot for `--check` (default: the latest).              |
| `--allow NAME`               | A benchmark exempt from `--check` (repeatable).                     |
| `--json`                     | With `--check`, emit the comparison as JSON.                        |

______________________________________________________________________

## `make compile-commands` and `make tidy`

`make compile-commands` copies cmake's compile database to the project root,
where clangd and clang-tidy look for it:

```sh
make compile-commands
```

It re-configures and re-copies every time. That is deliberate — the database
tracks the CMake source list, so a rule keyed on a timestamp goes stale the
moment you add a component.

`make tidy` runs clang-tidy over exactly the translation units cmake compiles,
refreshing the database first:

```sh
make tidy
```

The file list comes from the database rather than a directory walk, so a
generated `.c` that no CMake target builds is never linted into a false sense
of coverage.

The scaffolded `.clang-tidy` opts into `bugprone-*`, `cert-*` and
`clang-analyzer-*`, with the checks that misfire on jm's own layout turned off
and each one annotated with the construct it misfires on.

**It sets `WarningsAsErrors: "*"`**, so `make tidy` is a real gate — every
finding is an error.

It was off while jm's own generated C still had findings, because a `make tidy`
that fails on a project you just created is the fastest way to teach someone
never to run it again. That is fixed
([#944](https://github.com/just-buildit/just-makeit/issues/944)): a scaffold
with `--perf`, a module, a standalone object and a C app returns zero, measured
rather than assumed. A **freshly scaffolded** project reports clean; an
existing one very likely will not on the first run, and those findings are
about your code.

If a newer clang-tidy adds a check that fires on generated code, comment the
line out rather than working around the diagnostic — the file is yours (jm
writes it once and never rewrites it), and a scaffold going red on a toolchain
bump is not your bug to fix.

______________________________________________________________________

## `make coverage`

Generate C and Python coverage HTML reports. Run from the project root after
scaffolding with just-makeit.

```sh
make coverage
```

Requires `lcov`/`genhtml` for the C side and `pytest-cov` for Python:

```sh
sudo apt-get install lcov          # Debian/Ubuntu
brew install lcov                  # macOS
sudo pacman -S lcov                # Arch/CachyOS
uv add --dev pytest-cov
```

**What it does:**

1. Compiles a separate `build/cov/` tree with `-DCMAKE_C_FLAGS="--coverage -O0"` — the Release build in `build/` is untouched.
1. Runs CTest against the coverage binary.
1. Collects `.gcda` files with `lcov --capture`, strips system and test paths, and renders `docs/coverage/c/index.html` via `genhtml`.
1. Runs `pytest --cov=<package> --cov-report=html:docs/coverage/python`.

Both reports land under `docs/` (already in `.gitignore`).

______________________________________________________________________

## `make docs`

Build both C and Python API documentation. Run from the project root.

```sh
make docs
```

Requires `doxygen` for the C side and `zensical` + `mkdocstrings-python` for Python:

```sh
sudo apt-get install doxygen       # Debian/Ubuntu
brew install doxygen               # macOS
uv add --dev zensical mkdocstrings-python
```

**C API — Doxygen**

Reads `Doxyfile` (generated by just-makeit, edit freely) and produces
`docs/doxygen/html/index.html`. Covers every `*.h` and `*.c` under
`native/inc/` and `native/src/`. JavaDoc-style `/** @brief ... */` comments
in your C source appear automatically.

**Python API — Zensical + mkdocstrings**

Reads `zensical.toml` (generated by just-makeit) and produces `site/index.html`.
The generated `docs/api.md` page uses a single mkdocstrings directive:

```markdown
::: my_package
    options:
      show_source: true
      members: true
```

mkdocstrings introspects the compiled extension and renders docstrings from
`PyDoc_STR(...)` in the C binding.

Serve live with hot-reload:

```sh
zensical serve
```

______________________________________________________________________

## `just-makeit perf`

Upgrade an existing project to use performance annotations without
overwriting any user code. Must be run from the project root.

```sh
just-makeit perf
```

Writes `native/inc/jm_perf.h`, adds `#include "jm_perf.h"` to each object
header, and replaces `static inline` with `JM_FORCEINLINE JM_HOT` on `step()`.
Records `perf = true` in `just-makeit.toml` so future `object` and `add`
commands inherit it. Safe to run on a project with a filled-in `step()`.
Idempotent.

See [Performance annotations](../perf.md) for the full macro reference and
`JM_DEFINE_STEPS` documentation.

______________________________________________________________________

## `just-makeit apply`

Reconcile the generated files with `just-makeit.toml`. Use this after
hand-editing the manifest, after `git pull`, or to materialize any files
missing from a checkout. Must be run from the project root.

```sh
just-makeit apply
```

`apply` follows the **sacred / glue** contract:

| File                   | On every `apply`                                                                                                                                     |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `<comp>_ext.c`         | **Glue** — fully regenerated from the manifest.                                                                                                      |
| `src/<pkg>/<comp>.pyi` | **Glue** — fully regenerated.                                                                                                                        |
| `CMakeLists.txt`       | **Glue** — fully regenerated.                                                                                                                        |
| `<comp>_core.h`        | **Mixed** — a missing method/property *declaration* is injected; the inline `step()` body and the state struct are **sacred** and never re-rendered. |
| `<comp>_core.c`        | **Sacred** — never spliced or re-rendered once it exists. `steps()`/lifecycle bodies are yours.                                                      |

So editing the manifest always propagates to the glue, and `apply` injects any
missing method/property declaration into `_core.h`. The struct and inline
`step()` stay sacred. Changing a **signature** in TOML — or adding a **state
field** — is *structural*: rebuild the body from the manifest with
`jm regenerate` (or `jm add`, which is `regenerate` specialized for state). A
new method or computed property is additive instead — `jm method` /
`jm property` inject a declaration and append a fresh stub.

`apply` also warns about the files it did **not** touch: a
`native/tests/test_*_core.c` or `native/benchmarks/bench_*_core.c` that no
build file compiles, and a generated benchmark that records no measurement.
The first is marked `!` because it fails `jm status --check` — see
[Why `UNBUILT` gates](#why-unbuilt-gates).

It also warns before rendering over a `.pyi` that does not parse, naming the
hand-written members that will not survive — see
[Why `UNPARSEABLE` gates](#why-unparseable-gates-and-why-only-status-can-catch-it).

______________________________________________________________________

## `just-makeit regenerate <component>`

The deliberate-refresh half of the sacred/glue contract. Deletes every file
the component owns, then re-runs `jm apply` to rebuild them all from the
manifest. The manifest itself is left untouched (unlike `jm remove`). Works
for standalone and module objects. Must be run from the project root.

```sh
git stash                          # best-effort splice — stash first regardless
just-makeit regenerate engine
just-makeit regenerate engine --force            # skip the confirmation
just-makeit regenerate engine --discard          # clean reset, no splice
```

By default, hand-written bodies in `<comp>_core.c`/`<comp>_core.h`
(create/destroy/reset, `step()`, getters/setters, method implementations) are
lifted before the sacred files are deleted and spliced back into the freshly
regenerated ones, by function name. A signature change (e.g. from `jm add`
growing a lifecycle function's parameter list) is detected and skipped in
favor of the fresh body rather than force an incompatible splice. Pass
`--discard` for the old behavior — a clean reset back to the template
scaffold, with no preservation attempt. Either way, `git stash` or commit
first — the splice is best-effort text matching, not a guarantee. A single
confirmation guards the deletion; `--force` skips it.

| Flag      | Description                     |
| --------- | ------------------------------- |
| `--force` | Skip the deletion confirmation. |

______________________________________________________________________

## `just-makeit ci [--provider NAME]`

Generate a continuous-integration workflow that builds the project and runs
its tests (`make && make test`), so a scaffolded project is CI-green as fast
as it builds and tests locally. Must be run from the project root.

```sh
just-makeit ci                         # GitHub Actions: .github/workflows/ci.yml
just-makeit ci --provider woodpecker  # Woodpecker: .woodpecker.yml
just-makeit ci --force                 # overwrite an existing workflow file
```

The generated workflow installs the build dependencies and runs the same
build-and-test the [`test`](#just-makeit-test) target drives locally. If the
project enabled `pytest`, the dependency step also installs the Python test
requirements; otherwise it stays C-only.

| Flag              | Description                                                                            |
| ----------------- | -------------------------------------------------------------------------------------- |
| `--provider NAME` | `github` (default → `.github/workflows/ci.yml`) or `woodpecker` (→ `.woodpecker.yml`). |
| `--force`         | Overwrite the workflow file if it already exists.                                      |

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

## `just-makeit bind <component>`

Synthesise `<comp>_ext.c` and `<comp>.pyi` by reading `<comp>_core.h` directly,
without consulting `just-makeit.toml`. This is the "point at your C and get
Python" path. Must be run from the project root.

```sh
just-makeit bind engine          # synthesise engine_ext.c from engine_core.h
just-makeit bind engine --check  # exit 1 if the generated binding differs from the file on disk
```

`jm bind` parses the header for the standard just-makeit naming conventions
(`<comp>_state_t`, `<comp>_create`, `<comp>_step`, scalar field defaults from
the reset body) and renders the binding from the same context builders the
manifest-driven flow uses — so a bound `_ext.c` is byte-identical to a
scaffolded one.

**Current scope:** the simple processor shape — a state struct with scalar
fields, a `<comp>_create()` taking those fields in order, and a scalar-in /
scalar-out inline `step()`. If the header doesn't match that shape the parser
raises an error before touching any files.

**`--check` as a CI gate:** run `jm bind <comp> --check` in CI to ensure
the committed `_ext.c` never silently drifts from the header it was generated
from. Green means byte-identical; non-zero exit means regenerate and commit.

Shapes not yet supported (see [roadmap](../roadmap.md#now-write-it-in-c-get-python-jm-bind)):
methods, init_params, opaque state, variable-output, result-structs.

| Flag      | Description                                                                   |
| --------- | ----------------------------------------------------------------------------- |
| `--check` | Diff the synthesised binding against the file on disk; exit 1 if they differ. |

______________________________________________________________________

## `just-makeit status`

Show which files in the project tree have drifted from what `jm apply` would
generate — a read-only drift report. Must be run from the project root.

```sh
just-makeit status
```

| Flag                | Description                                                                                                                                                                                                                                                                                                                   |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--allow PATH`      | Treat `PATH` (exact path or fnmatch glob) as a known deviation: reported as `ALLOWED`, not counted. Repeatable; combines with `[project] status_allow` in the manifest.                                                                                                                                                       |
| `--json`            | Emit the report as JSON (`{path, state, allowed, dropped_symbols}` per entry) instead of a table.                                                                                                                                                                                                                             |
| `--diff`            | Print a unified diff per stale file.                                                                                                                                                                                                                                                                                          |
| `--check`           | One-line summary only — the per-file listing is suppressed, the exit code is unchanged. CI mode.                                                                                                                                                                                                                              |
| `--strict-examples` | Promote "an authored `@code` line is too wide for its generated stub" from a reported count to a **failure** (gh-760). This is the one-off form; `[project] strict_examples = "true"` in the manifest is the durable one, so a project that wants the stricter reading does not have to remember the flag at every call site. |

Prints a table of files, each in one of these states:

| Status        | Meaning                                                                                                                                                                                                                                                                                                                                                                              | Gates CI? |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------- |
| `OK`          | `apply` would leave the file untouched.                                                                                                                                                                                                                                                                                                                                              | no        |
| `MISSING`     | `apply` would create it — declared in the manifest, absent on disk.                                                                                                                                                                                                                                                                                                                  | yes       |
| `STALE`       | `apply` would rewrite it from the manifest (glue regenerated, `_core.h` declarations merged).                                                                                                                                                                                                                                                                                        | yes       |
| `ALLOWED`     | A `MISSING`/`STALE` file matched `--allow` or `[project] status_allow` — reported, but excluded from the drift count.                                                                                                                                                                                                                                                                | no        |
| `DROPPED`     | A stale `.pyi` whose on-disk class/method/function has no manifest trace and would vanish on regen (gh-426). This is content loss, not routine drift, so it is **never** suppressed by `--allow` or `status_allow`.                                                                                                                                                                  | yes       |
| `DRIFT`       | An init-param default in the manifest disagrees with the default documented in the component's `_core.h` (gh-442). jm can't tell which side is stale — fix one to match. Also never suppressible.                                                                                                                                                                                    | yes       |
| `UNBUILT`     | A `native/tests/test_*_core.c` or `native/benchmarks/bench_*_core.c` that no build file compiles (gh-806) — usually a renamed component's real suite, left behind while a fresh scaffold took over its target.                                                                                                                                                                       | yes       |
| `SILENT`      | A generated benchmark that records no measurement: the component has no `step()` and none of its methods has a benchable shape, so the target writes an empty `"benchmarks": []` array (gh-806). The file itself carries a `TODO:` naming the candidate methods and a worked `jm_bench_add` example (gh-840) — `SILENT` is the to-do list; the file is the instructions.             | no        |
| `UNPARSEABLE` | A `.pyi` on disk that is not valid Python **and** holds hand-written members (gh-785). jm finds a stub's members with `ast`, so it can find none in this one and the next `jm apply` renders over them. Never suppressible.                                                                                                                                                          | yes       |
| `NOTE`        | A method sets `pass_capacity` while its header still declares `max_out(state)` (gh-921), so the exact allocation the opt-in asks for is not the one generated. Nothing is broken — see below — so this is a note, never counted and never printed under `--check`.                                                                                                                   | no        |
| `OUTDATED`    | A **create-only** file whose content is jm's own — the `Makefile`, `.clang-tidy`, `.clang-format`, `jm_test.h`, `jm_bench.h`, `jm_perf.h`, `jm_simd.h`, the common headers, the cmake `.in` templates — and which differs from what this jm renders (gh-949). `apply` never rewrites a create-only file, so adopting the new version is your call; suppressible with `status_allow`. | no        |

| `UNANCHORED` | The top `CMakeLists.txt` has lost a sentinel jm splices against — `# ── Components` or `# ── Modules` (gh-975). Every splice treats a missing anchor as nothing to do, so the wiring was never written and a module with no `add_subdirectory()` is not built at all. Put the line back, or keep that wiring yourself and name the file in `status_allow`. | yes |

| `UNWIRED` | A component declares a `<X>_core` OBJECT library that the top `CMakeLists.txt` folds into no combined C library (gh-984), so its symbols ship in neither `lib<pkg>.so` nor `lib<pkg>.a` while its header installs anyway. Python is unaffected — the extension links each core directly — which is why this hides. `jm apply` writes the missing `target_sources()` line. Suppressible per component with `CMakeLists.txt:<core>`, or wholesale with `CMakeLists.txt` if you link your cores your own way. | yes |

| `DANGLING` | The top `CMakeLists.txt` wires a `<X>_core` that no component declares (gh-984) — an interrupted removal or a bad merge. cmake resolves `$<TARGET_OBJECTS:>` at **configure** time, so the project does not build at all. `jm apply` drops the line. Never suppressible. | yes |

The exit code is the count of gating drift, so `jm status --check` is a
drop-in CI gate: zero means `jm apply` is a no-op.

### `NON-ASCII NAMES` — an advisory, not drift

Alongside the file table, `jm status` lists every **declared name** outside
ASCII — object, module, function, method, property, view class, state field
and init param. A name is not a file `apply` would rewrite, so this is never
counted in the exit code and never appears under `--check`.

```text
NON-ASCII NAMES (1) — already declared, portability risk:
  ? method 'café'
  GCC accepts UTF-8 identifiers as an extension and MSVC differs, so these can
  compile on one toolchain and not another. Rename them to ASCII; jm no longer
  accepts one, so `apply` refuses the manifest until you do. Not counted as drift.
```

It is printed **before** the report's other sections, and that ordering is
load-bearing: `status` computes drift by replaying `apply` on a scratch copy,
and since gh-784 that replay *refuses* a manifest carrying such a name. Were
the list printed in table order it would never be reached by the one project
that needs it, and renaming would proceed one `error:` at a time instead of
from a complete list.

Shipped in v0.55.0, a release ahead of the rejection, so a project could
rename on its own schedule. See *Naming rules* under
[`jm object`](scaffold.md).

### `NOTE` — an opt-in that cannot take effect

`pass_capacity` hands the kernel its output capacity so the binding can trust
`max_out()` exactly, allocating it instead of the defensive `max(max_out, n)`.
That trust needs `max_out` to be able to *see* the call, which is the other
half of gh-607: the count parameter. A project can sit between the two — the
manifest opts in, the sacred header still declares the pre-gh-607
`max_out(state)`:

```text
NOTE (1) — `pass_capacity` is set but cannot take effect:
  . nco.steps_u32: nco_steps_u32_max_out(state) cannot see the call, so the
      allocation stays clamped to max(max_out, n).
```

Since gh-920 this is **safe**: a bound that cannot depend on `n` is not read as
a per-call one, so the clamp stays and nothing truncates. Before that fix it
was not — doppler's `NCO.steps_u32(393_216)` returned 65536 samples and raised
nothing. What remains is only that the opt-in is inert and says so nowhere, so
`status` says it. Two ways out, both in the message: give `max_out` the count,
or declare `exact_max_out` if the bound really is call-independent.

It is a note rather than a gate because it cannot be wrong — the arity is read
off the header's own declaration and the flag off the manifest, with nothing
inferred between them — and because the allocation it describes is correct.
Failing CI over a correct allocation would be worse than the silence.

It lives here rather than on `jm apply` for the same reason `NON-ASCII NAMES`
does: the condition is per method, and on a tree with dozens of
variable-output methods an apply-time note is a wall of lines arriving exactly
when the reader is watching for what changed. This is a standing property of
the manifest, not an event.

### Why `UNBUILT` gates

Renaming a component moves its manifest section and its native directories,
but its C test and benchmark keep their old filenames. `jm apply` then
materialises `test_<new>_core.c` / `bench_<new>_core.c` and re-renders the
CMake that builds *those* names — so the author's real files stay on disk,
compiled by nothing, and a scaffold takes over the target.

The scaffold **passes**. `ctest` reports "100% tests passed" with the real
suite missing from the denominator, and `make bench` exits 0 having measured
nothing. Every other finding here shows up as something visibly wrong
somewhere; this one shows up as a green CI run, which is why it is the one
finding whose report had to be a gate rather than a note.

If a file is deliberately kept unbuilt, name it in `[project] status_allow`:
it stays listed, marked `[status_allow]`, and stops counting.

`UNBUILT` reports a benchmark orphan only when the tree builds **some**
benchmark. A project that builds none — one predating the `make` backend's
`bench:` target (gh-832) — has the whole category unbuilt by construction, and
failing the gate for something no `jm apply` can clear is the thing this
deliberately does not do. It arms itself the moment the project gains bench
rules.

### Why `UNPARSEABLE` gates, and why only `status` can catch it

Every other finding on this page describes something `jm apply` fixes. This
one describes something `jm apply` **consumes**.

jm preserves a stub's hand-owned members — `manual_stub` methods and any
member marked `# jm:hand` — by parsing the old file with `ast` and
transplanting the text back over the fresh render. A stub that does not parse
has no members to find, so the render replaces all of them. Then the file jm
writes *does* parse, the finding disappears, and the next `jm status` is
clean over a tree that has lost members no manifest can put back — a
`# jm:hand` member has no manifest declaration at all.

So `status` is the last moment anything can say so while the content is still
on disk:

```
UNPARSEABLE (1) — .pyi file(s) that do not parse, holding 2 hand-written member(s):
  ! src/sp/thing.pyi: line 64: invalid syntax
      - execute_ci16
      - execute_special
```

**Fix the syntax error first and every one of them survives.** Run `jm apply`
first and they are gone; `jm apply` says so as it happens, but by then the
only copy is in version control.

jm warns rather than refusing here, unlike the sibling check that guards a
*parseable* stub (gh-765, which raises). A stub that is not valid Python is
itself broken, and regenerating it is the natural repair — refusing would
block the recovery path for exactly the situation that produces it. A broken
stub with nothing hand-owned in it is therefore repaired silently, as routine
`STALE` drift.

Two runtime signals back this up in newly scaffolded components, so a CI log
tells a placeholder from a suite without anyone running `jm status`:

- a generated C test prints its assertion count — `PASSED (4 checks)` — plus
    a `no assertions beyond the N just-makeit generated` note that clears itself
    the moment an author adds a check of their own. Both the counters and that
    note live in `native/tests/jm_test.h`, written once per project (gh-934);
- `jm_bench_write_json` prints `no measurements recorded` when a benchmark
    timed nothing.

Both live in create-only files, so they reach components scaffolded from
v0.52.0 onward. Existing trees are covered by the `UNBUILT` / `SILENT` scan,
which needs nothing but the files already on disk.

`status` never writes anything (it runs `apply` against a throwaway copy of
the tree); it is always safe to run. Use it to confirm that `jm apply` is a
no-op before a release, or to see what changed after a manual edit to
`just-makeit.toml`.

______________________________________________________________________

## `just-makeit script`

Print a shell script to stdout that fully reconstructs the current project
from scratch via CLI commands. Must be run from the project root.

```sh
just-makeit script              # print to stdout
just-makeit script > rebuild.sh # save to file
```

Reads `just-makeit.toml` and emits one command per scaffold step in the
correct category order: `new` → `module` → `object` → `method` → `property` →
`function`. The output is a valid shell script that, when run from the
parent directory, produces an identical `just-makeit.toml`. Within the
`object` category, order matches original creation order for a
`--no-fragments` (single-manifest) project; under the default fragment
layout, objects are read back from `objects/*.toml` and so are reconstructed
in filename (alphabetical) order instead — harmless for correctness (each
`object` command is independent) but the sequence may not match how you
originally typed it.

**Note:** `--impl` / `--replace` are not stored in `just-makeit.toml` (the
lifted body is patched directly into the generated files), so they are not
reproduced. Implemented function and step bodies are preserved in your C
source files and are unaffected.

**Example**

Given a project with two objects, `just-makeit.toml` looks like:

```toml
[project]
name = "dsp_toolkit"
version = "0.1.0"
build = "cmake"

[gain]
arg_type = "float"
return_type = "float"

[[gain.state]]
name = "gain"
type = "float"
default = "1.0"

[ema]
arg_type = "float"
return_type = "float"

[[ema.state]]
name = "alpha"
type = "double"
default = "0.1"

[[ema.state]]
name = "prev"
type = "float"
default = "0.0"
```

`just-makeit script` produces:

```sh
#!/usr/bin/env sh
# Reconstructed from just-makeit.toml

just-makeit new dsp_toolkit
cd dsp_toolkit

just-makeit object gain \
    --state gain:float:1.0 \
    --arg-type float
just-makeit object ema \
    --state alpha:double:0.1 \
    --state prev:float:0.0 \
    --arg-type float
```

Running that script from the parent directory recreates the project structure
and an identical `just-makeit.toml`.

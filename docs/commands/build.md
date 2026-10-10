# Build & tooling commands

______________________________________________________________________

## `just-makeit build [dir]`

Build the C extensions and package a wheel.

```sh
just-makeit build           # wheel → dist/
just-makeit build wheels/   # wheel → wheels/
```

Configures CMake (if not already done), builds the C extensions, then
packages the wheel with
[just-buildit](https://github.com/just-buildit/just-buildit). just-buildit is
not a just-makeit dependency: `pip install just-buildit` first, or the command
stops and says so. Must be run from a project directory containing
`pyproject.toml`.

______________________________________________________________________

## `just-makeit test`

Build (if needed), then run all tests.

```sh
just-makeit test
```

- CTest runs the C tests (`native/tests/`).
- pytest, if it is importable in the running Python (else
    `unittest discover`), runs the Python tests under `src/`. Extra arguments
    are passed to pytest: `just-makeit test -k fir`.

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
just-makeit bench              # everything runnable, both sides, saves a snapshot
just-makeit bench fir biquad   # only these
just-makeit bench util         # ...including a non-component benchmark
```

Each run rebuilds via `cmake`, executes every `bench_<comp>_core` C binary and
the `pytest-benchmark` suite under `src/`, prints a stats table per side with a
Δ column against the previous snapshot, and writes two immutable files —
`<tag>.json` (Python) and `<tag>-c.json` (C), where `<tag>` is a UTC timestamp.
Commit them to keep the history.

**What gets run is what gets built (gh-1023).** The C side is not limited to
manifest components. jm takes every `native/benchmarks/bench_<X>_core.c` that
some build file compiles, so a benchmark for a `[project] c_deps` directory —
whose `CMakeLists.txt` is hand-written, since jm emits only the
`add_subdirectory` line — is picked up the day it is written, with nothing to
declare. Discovered names are listed as `extra` at the top of the run, and are
selectable positionally like any component.

This mirrors the Python side, which has always discovered by `pytest`
collection rather than by declaration, and it is the reason there is no
`[project.bench]` key for it: a benchmark is not manifest-owned
(`jm status --check` does not track one), and a list you must remember to
append to goes stale in exactly the silent way this fixed.

Discovery reads build files as **text**, so it is a heuristic: a target named
only inside a comment still looks built. A discovered name is therefore
allowed to be wrong but not allowed to be expensive — if its target does not
build, jm prints `skip` and carries on, where a manifest component's target
failing to build is still a hard error. The reference match is word-anchored,
so `bench_fir_core` is not confused with `bench_fir_core_simd`.

**jm does not read build files under `vendor/`** (gh-1031), any more than it
reads `build/`. A vendored dependency is somebody else's build system, and
reading it was actively harmful: one vendored `file(GLOB ...)` stood the scan
down for the whole tree, which also silenced the `UNBUILT` detector below.

jm deliberately does not try to work out whether a given wildcard could reach
the directory being scanned. That would mean inferring a foreign build
system's semantics, and it fails in the expensive direction — a pattern read
as irrelevant that in fact matches makes jm call a compiled file unbuilt.
`third_party/` and `external/` are the same situation under a different name
and are still read; that is a known gap with an obvious fix, not a wrong
answer.

The one case jm cannot answer at all is a build file that enumerates sources by
wildcard — `file(GLOB ...)` makes "is this compiled?" unanswerable by reading.
There the scan stands down to manifest components only and **says so**, rather
than quietly running fewer benchmarks than the tree holds. It is the same
stand-down the `UNBUILT` detector makes, for the same reason — and since
gh-1033 that one says so too, as `UNCHECKED`.

**Gate mode.** `--check` compares against a baseline instead of saving and
exits non-zero on a regression, so CI can fail a change that slows a kernel
down:

```sh
just-makeit bench --check --threshold 0.10   # fail if anything is >10% slower
```

`--check` fails on two things, not one. The obvious one is a benchmark that got
slower. The other is a benchmark that **stopped running** (gh-1029): a name the
baseline carries and this run did not produce is reported as `missing` and
fails the gate.

That second half exists because the comparison used to be keyed on the current
run alone, which made the gate's coverage a function of whatever the run
happened to produce — so shrinking it always looked like success. A target that
stopped building, a binary that wrote no JSON, a kernel dropped from `main()`,
or a benchmark renamed (retiring the old name and reporting the new one as
`new`, which never fails) all read as "no regression".

A deletion you meant is a deliberate act, so it carries a flag:

```sh
just-makeit bench --check --allow fir::step_64k   # this one is gone on purpose
```

`--allow` is the whole escape hatch — the same one a deliberately slower
benchmark uses. The noise floor does not apply here: it answers "is this timing
trustworthy?", and an absent benchmark has no timing to distrust.

**Slow hardware: the run budget (gh-1687).** Each benchmark run — one
`bench_<comp>_core` binary, or the one `pytest --benchmark-only` — gets 600 s
by default. That was sized on a desktop core; a Cortex-A53-class board runs the
same binary 10–20× slower, so the budget is yours to set:

```toml
# just-makeit.toml — every run
[project.bench]
timeout = 3600   # seconds; 0 = no limit
```

```sh
just-makeit bench --timeout 0    # this run only, no limit
```

A run past the budget costs **that benchmark and nothing else**: it is killed
and reported as `timeout bench_<comp>_core`, the remaining benchmarks still
run, the ones that finished are saved (the snapshot lists what timed out under
`"timed_out"`), and the command then exits 1 naming it — so the numbers you did
get are kept and the failure is still loud. Under `--check` the timed-out
benchmark's baseline entries are `missing`, and the gate fails. The timed-out
binary's own sections are lost: `jm_bench_write_json` writes once, at the end
of `main`. The Python side is one `pytest` run, so a timeout there loses the
Python results for that run.

The builds `jm bench` drives are **not** timed: a cold build on the same board
took 603 s, and a build that is slow has not failed. Bound a CI job's wall
clock with the job's own `timeout-minutes:`.

**`silent` from the artifact (gh-1691).** A benchmark that runs and writes an
empty `"benchmarks": []` is named as `silent bench_<comp>_core`. That is
read from the JSON the binary wrote, so it holds however the source records —
including through a helper of your own that wraps `jm_bench_add`. It is the
authoritative version of the `SILENT` advisory `jm status` gives from source.

**Arguments**

| Argument                     | Description                                                                                                                                          |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `comp …`                     | Restrict to the named components (default: all).                                                                                                     |
| `--tag TAG`                  | Snapshot tag (default: a UTC timestamp).                                                                                                             |
| `--c-only` / `--python-only` | Run only one benchmark side.                                                                                                                         |
| `--check`                    | Compare against a baseline and exit 1 on regression; saves nothing.                                                                                  |
| `--threshold N`              | Fractional slowdown that fails `--check` (default `0.10` = 10%).                                                                                     |
| `--baseline TAG`             | Baseline snapshot for `--check` (default: the latest).                                                                                               |
| `--allow NAME`               | A benchmark exempt from `--check` (repeatable).                                                                                                      |
| `--json`                     | With `--check`, emit the comparison as JSON.                                                                                                         |
| `--timeout S`                | Seconds one benchmark run may take before it is skipped and the run fails at the end (default: `[project.bench] timeout`, else 600; `0` = no limit). |

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

Writes `native/inc/<pkg>/jm_perf.h` and `native/inc/<pkg>/jm_simd.h`, adds
`#include "<pkg>/jm_perf.h"` to each object header, and replaces
`static inline` with `JM_FORCEINLINE JM_HOT` on `step()`. Records
`perf = "true"` in `just-makeit.toml` so future `object` and `add` commands
inherit it. Safe to run on a project with a filled-in `step()`.
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
just-makeit apply path/to/fragment.toml   # compose an external fragment first
just-makeit apply --only=fir              # reconcile one component or module
```

Given a fragment path, `apply` copies the file into `objects/`, adds it to
`include`, then materializes as usual. `--only=NAME` restricts the
per-component regeneration to that component (or module); the aggregate files
— the package `__init__.py`, the root `CMakeLists.txt`, the umbrella header —
are still updated. See
[What `jm apply` does](../declarative-scaffolding.md#what-jm-apply-does).

`apply` follows the **sacred / glue** contract:

| File                               | On every `apply`                                                                                                                                                                            |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `<comp>_ext.c`                     | **Glue** — fully regenerated from the manifest.                                                                                                                                             |
| `src/<pkg>/<comp>.pyi`             | **Glue** — fully regenerated.                                                                                                                                                               |
| `native/src/<comp>/CMakeLists.txt` | **Glue** — fully regenerated.                                                                                                                                                               |
| root `CMakeLists.txt`              | **Mixed** — only jm's marked blocks (components, modules, install) are rewritten; the rest is yours.                                                                                        |
| `<comp>_core.h`                    | **Mixed** — a missing method/property *declaration* is injected; the inline `step()` body and the state struct are **sacred** and never re-rendered.                                        |
| `<comp>_core.c`                    | **Sacred** — never re-rendered. The only write is appending a stub for a manifest-declared method it lacks (gh-1294); existing bodies, `steps()` and lifecycle included, are never touched. |

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
scaffold, with no preservation attempt. On a module object `--discard` also
rebuilds the binding fragments that call into its core, its own
`<module>_ext_<obj>.c` and each view's, so a constructor change reaches all
of them (gh-965, gh-2073); a hand-written `*_extra.c` is kept. `jm add` and
`jm remove state` rebuild this way. Either way, `git stash` or commit
first — the splice is best-effort text matching, not a guarantee. A single
confirmation guards the deletion; `--force` skips it.

A regenerate that fails does not cost you the component. When the rebuild's
`apply` refuses after the delete — over another component's manifest row,
say — jm puts back every file it had deleted or written and says so. An
`impl_file` naming a file inside the component is refused before anything
is deleted, since the rebuild could not read the body back from a file it
had just removed (gh-1867).

An object in a [`no_generate`](../configuration.md#no_generate-hand-written-extension-modules)
module is refused, by `regenerate` and by every command that rebuilds through
it, before anything is asked or written: `jm apply` writes none of that
module back, so the deletion would be all that happened (gh-2087). Its C is
yours; edit it by hand.

| Flag        | Description                                                                |
| ----------- | -------------------------------------------------------------------------- |
| `--force`   | Skip the deletion confirmation.                                            |
| `--discard` | Skip the lift-and-splice; reset the sacred files to the template scaffold. |

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
build-and-test the [`test`](#just-makeit-test) target drives locally. The
dependency step installs numpy, plus pytest if the project enabled `pytest`.

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

| Key       | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| --------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `version` | The project version. Writes `[project] version` and every generated copy `status` checks for it (`pyproject.toml`, `bootstrap.toml`, the root `CMakeLists.txt`'s `project(VERSION)`, the `Doxyfile`, `<pkg>_version()`), replacing only the value, and re-renders a `pep723` app (gh-2069). A copy the build derives is left alone; a copy that cannot hold the value (CMake takes integers only, so a pre-release) is named and left, and stays a `VERSION` finding. |

______________________________________________________________________

## `just-makeit bind <component>`

Synthesise `<comp>_ext.c` and `<comp>.pyi` by reading `<comp>_core.h` directly,
without consulting `just-makeit.toml`. This is the "point at your C and get
Python" path, for a header the manifest does not declare. Must be run from the
project root.

```sh
just-makeit bind engine          # synthesise engine_ext.c from engine_core.h
just-makeit bind engine --check  # exit 1 if the generated binding differs from the file on disk
```

`jm bind` parses the header for the standard just-makeit naming conventions
(`<pkg>_<comp>_state_t`, `<pkg>_<comp>_create`, `<pkg>_<comp>_step`, scalar
field defaults from the reset body) and renders the binding from the same
context builders the manifest-driven flow uses — so a bound `_ext.c` is
byte-identical to a scaffolded one.

**A component the manifest declares is refused**, `--check` included: its
binding is `jm apply`'s, rendered from the manifest, and a warning, a
`create()` error or a record type is nothing a header says, so a render from
the header alone would drop it. `jm bind` exits 1 naming `jm regenerate <comp>`
and `jm apply`, and writes nothing (gh-2072).

**With no `just-makeit.toml`** -- a bare `native/` tree beside a
`pyproject.toml` -- `jm bind` reads the project off the tree (gh-1895). The
header layout is where the header is: `native/inc/<pkg>/<comp>/<comp>_core.h`
(what `jm new` writes) or the older `native/inc/<comp>/<comp>_core.h`, and the
binding's `#include`s are spelled for that layout. The C prefix is the one the
header's `_state_t` declares: `<prefix>_<comp>_state_t` binds as
`c_prefix = "<prefix>"`, a bare `<comp>_state_t` as none. When neither layout
holds the header, or both do, it exits 1 naming both paths and writes nothing.

**Current scope:** a state struct with scalar and opaque-pointer fields, or
a forward-declared (opaque) one; a `<pkg>_<comp>_create()` taking the state
fields in order, where a parameter that matches no field becomes an init
param; a scalar-in / scalar-out inline `step()`; getters and setters
(`<pkg>_<comp>_get_<name>` / `<pkg>_<comp>_set_<name>`); any other
`<pkg>_<comp>_<verb>(state, ...)` whose return type and single optional
scalar argument parse, as a method; and a verb with a
`<pkg>_<comp>_<verb>_max_out` sibling, as a variable-output method. A
declaration it finds but cannot parse is skipped with a warning rather than
failing the run. A method the header cannot express needs the manifest, and
once the manifest declares the component its binding is `jm apply`'s.

**`--check` as a CI gate:** run `jm bind <comp> --check` in CI to ensure
the committed `_ext.c` never silently drifts from the header it was generated
from. Green means byte-identical; non-zero exit means regenerate and commit.
A binding that is not on disk at all is a finding too: `--check` exits 1 on
one line naming the `_ext.c` `jm bind <comp>` would write (gh-2101).

Not yet supported (see [roadmap](../roadmap.md#now-write-it-in-c-get-python-jm-bind)):
methods with more than one parameter or with an array parameter, and
result-struct returns.

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

| Flag                | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--allow PATH`      | Treat `PATH` (exact path or fnmatch glob) as a known deviation: reported as `ALLOWED`, not counted. Repeatable; combines with `[project] status_allow` in the manifest.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `--json`            | Emit the report as JSON (`{path, state, allowed, dropped_symbols}` per entry) instead of a table.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `--diff`            | Print a unified diff per stale file, and the root `CMakeLists.txt` against today's render when `ROOT CMAKE` names a fix it lacks.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `--check`           | CI mode: the summary, plus the MISSING and STALE files the exit code counts (gh-1619) -- the advisory listings are suppressed. Exits 1 on drift.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `--shared-cores`    | List component cores statically linked into more than one extension module (gh-1117). CPython imports extensions `RTLD_LOCAL`, so each `.so` holds its own copy of every file-scope `static` in those cores — correct for a pure kernel, silently wrong for a primitive whose contract is one-per-process. **Opt-in and never counted**, both measured: over doppler's manifest it lists 45 cores across 33 modules, almost all of them correctly, so printing it by default would be noise and counting it would fail a green project forever. Always present in `--json`, where a machine reader can filter to the core it cares about. jm reports the linkage it owns and does not read your C to guess which core is which. Declaring the one that matters: [Shared process state](../shared-state.md). |
| `--strict-examples` | Promote "an authored `@code` line is too wide for its generated stub" from a reported count to a **failure** (gh-760). This is the one-off form; `[project] strict_examples = "true"` in the manifest is the durable one, so a project that wants the stricter reading does not have to remember the flag at every call site.                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `--docs`            | List every property, record field, method and module function whose docstring is only its name, and where each would read its text from (gh-1394). Read-only, and it exits 0 whatever it finds: whether an undocumented member deserves a sentence is the author's call, and a gate here would fail a project on the day it declares a property it has not documented yet. A field's text comes from its OWN struct's trailing `/**< ... */` (gh-1300) — a same-named field in another struct, or in a header yours includes, is not consulted, so this report is how you find the ones nothing documents. Class and module docs are not walked yet (gh-1396).                                                                                                                                              |

Prints a table of files, each in one of these states:

| Status                | Meaning                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | Gates CI? |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------- |
| `OK`                  | `apply` would leave the file untouched.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | no        |
| `MISSING`             | `apply` would create it — declared in the manifest, absent on disk.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | yes       |
| `STALE`               | `apply` would rewrite it from the manifest (glue regenerated, `_core.h` declarations merged) or, for a sacred `_core.c`, append a definition the manifest declares and the file lacks (gh-1294).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | yes       |
| `ALLOWED`             | A `MISSING`/`STALE` file matched `--allow` or `[project] status_allow` — reported, but excluded from the drift count.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | no        |
| `DROPPED`             | A stale `.pyi` whose on-disk class/method/function has no manifest trace and would vanish on regen (gh-426). This is content loss, not routine drift, so it is **never** suppressed by `--allow` or `status_allow`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | yes       |
| `DRIFT`               | An init-param default in the manifest disagrees with the default documented in the component's `_core.h` (gh-442). jm can't tell which side is stale — fix one to match. Also never suppressible.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | yes       |
| `VERSION`             | A generated file's copy of `[project] version` disagrees with it (gh-1141) — `pyproject.toml`, the top `CMakeLists.txt`'s `project(... VERSION)`, `bootstrap.toml`, the `Doxyfile`'s `PROJECT_NUMBER`, or `<pkg>_version()` in `native/src/<pkg>_lib.c`. All five are create-only, so `apply` rewrites none of them and a bump reaches none of them. jm can't tell which side is stale — a release bumps `pyproject.toml` and never the manifest, so it is often the manifest — so it names both and rewrites neither. Suppressible per file with `status_allow`. Omitting `[project] version` from `just-makeit.toml` makes jm read it from `pyproject.toml` instead, which removes that file from this check entirely (gh-1283) — see [configuration](../configuration.md).                                                                                         | yes       |
| `ORPHAN`              | A `native/inc/<pkg>/<comp>/<comp>_procglobal.h` on disk for a component that no longer declares `process_global` (gh-1142). The same `apply` that dropped the key stripped the rendezvous from every generated `PyInit_`, so the header — stamped `DO NOT EDIT` — now describes one that is not generated and names a publisher that does not publish. It still compiles; what breaks is a hand-written binding that follows it. jm deletes nothing: remove the file, or re-declare the key.                                                                                                                                                                                                                                                                                                                                                                          | yes       |
| `DOC`                 | A manifest `doc` carries a numpy section heading (`Parameters` / `----------`) (gh-1154, gh-1493). A `doc` renders **verbatim** on every face, so the heading lays out correctly -- and jm then appends the section it generates itself, so the docstring has two. Write the prose in `doc` and let jm generate the sections, or put the full docstring as Doxygen above the declaration in the component's `_core.h`. Never reported where the `doc` is the member's whole docstring and jm generates nothing beside it: an `extra_methods` row, a property, a capsule's methods (gh-2059). Suppressible per entry with `status_allow`.                                                                                                                                                                                                                              | yes       |
| `UNBUILT`             | A `native/tests/test_*_core.c` or `native/benchmarks/bench_*_core.c` that no build file compiles (gh-806) — usually a renamed component's real suite, left behind while a fresh scaffold took over its target.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | yes       |
| `CTOR`                | The `<pkg>_<obj>_create()` declaration in the sacred `native/inc/<pkg>/<obj>/<obj>_core.h` takes different parameters from the ones the manifest renders (gh-1076). jm **injects** that declaration, so this is jm verifying what it wrote. It cannot tell which side is stale — fix one to match. Never suppressible: while they disagree, `jm regenerate <obj>` emits a `create()` that does not compile against your `_core.c`.                                                                                                                                                                                                                                                                                                                                                                                                                                    | yes       |
| `UNCHECKED`           | The `UNBUILT` scan above did not run (gh-1033): a build file enumerates its sources by wildcard, so "is this compiled?" cannot be answered by reading. Reported because the absence of an `UNBUILT` section otherwise means "not checked" and "checked and clean" indistinguishably. Not a gate — no `jm apply` clears a wildcard; name your sources explicitly to put the tree back under the gate.                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | no        |
| `SILENT`              | A generated benchmark that records no measurement: the component has no `step()` and none of its methods has a benchable shape, so the target writes an empty `"benchmarks": []` array (gh-806). The file itself carries a `TODO:` naming the candidate methods and a worked `jm_bench_add` example (gh-840) — `SILENT` is the to-do list; the file is the instructions. Read from source, so it says only what it can see: the `jm_bench_t` accumulator passed to `jm_bench_write_json` is declared in the file and touched by nothing else. Hand it to anything — `jm_bench_add`, or a helper of your own, in the file or a header — and it is not reported (gh-1691); `jm bench` reports a binary that in fact recorded nothing as `silent`, from its JSON.                                                                                                        | no        |
| `UNPARSEABLE`         | A `.pyi` on disk that is not valid Python **and** holds hand-written members (gh-785). jm finds a stub's members with `ast`, so it can find none in this one and the next `jm apply` renders over them. Never suppressible.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | yes       |
| `NOTE`                | A method sets `pass_capacity` while its header still declares `max_out(state)` (gh-921), so the exact allocation the opt-in asks for is not the one generated. Nothing is broken — see below — so this is a note, never counted and never printed under `--check`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | no        |
| `OUTDATED`            | A **create-only** file whose content is jm's own — the `Makefile`, `.clang-tidy`, `.clang-format`, `.gitignore`, `.gitattributes`, `CMakePresets.json`, `Doxyfile`, `zensical.toml`, `bootstrap.toml`, `jm_test.h`, `jm_bench.h`, `jm_perf.h`, `jm_simd.h`, `clib_common.h`, `pyex_common.h` — and which differs from what this jm renders (gh-949). The packaging templates are reported under `PACKAGING` instead. `apply` never rewrites a create-only file, so adopting the new version is your call; suppressible with `status_allow`.                                                                                                                                                                                                                                                                                                                           | no        |
| `ROOT CMAKE`          | A fix jm's root `CMakeLists.txt` template carries outside its marked blocks and the project's root file lacks (gh-1459, gh-1471): the combined library's `libm` link (gh-1452), and the Windows build's static library name, DLL exports, default build type, release CRT, complex-range flag and CRT/math defines (gh-1368), and `install-block`: the install section is not jm's managed block, so no packaging fix reaches it (gh-1589; `jm adopt --packaging`). Outside those blocks the file is yours, so `apply` never adds them; each line says what breaks without the fix and where, and `--diff` prints the file against today's render to merge from. Read as CMake commands rather than text, so a formatter's layout or your own equivalent spelling is not reported. Suppress one fix with `CMakeLists.txt:<fix>` in `status_allow`.                    | no        |
| `LINE ENDINGS`        | A file that differs from jm's render **only** in CRLF versus LF (gh-1641) -- what a Windows checkout without the project's `.gitattributes` gives every file. No compiler, CMake, Python or formatter reads the two differently, so it is neither `STALE` nor `OUTDATED`; `--check` prints the count, the full report the files. `apply` rewrites the files it regenerates as LF and lists them as `eol`. See [Windows](../windows.md#line-endings).                                                                                                                                                                                                                                                                                                                                                                                                                  | no        |
| `UNANCHORED`          | The top `CMakeLists.txt` has lost a sentinel jm splices against — `# ── Components` or `# ── Modules` (gh-975). Every splice treats a missing anchor as nothing to do, so the wiring was never written and a module with no `add_subdirectory()` is not built at all. Put the line back, or keep that wiring yourself and name the file in `status_allow`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | yes       |
| `UNWIRED`             | A component declares an OBJECT library (in its CMakeLists, or its `<dir>_extra.cmake` hook) that no `CMakeLists.txt` folds into a combined C library (gh-984), so its symbols ship in neither `lib<pkg>.so` nor `lib<pkg>.a` while its header installs anyway. Python is unaffected — the extension links each core directly — which is why this hides. For a core jm generates, `jm apply` writes the missing `target_sources()` line; for any other -- a `no_generate` module's, a c_dep's, your own -- the listing says `jm apply` writes no line and prints the lines to add to the root `CMakeLists.txt` (gh-1626). Which applies is read from the replay `status` runs: a core that replay still declares and wires (gh-1840). Suppressible per component with `CMakeLists.txt:<core>`, or wholesale with `CMakeLists.txt` if you link your cores your own way. | yes       |
| `DANGLING`            | The top `CMakeLists.txt` wires a `<X>_core` that no component declares (gh-984) — an interrupted removal or a bad merge. cmake resolves `$<TARGET_OBJECTS:>` at **configure** time, so the project does not build at all. `jm apply` drops the line. Never suppressible.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | yes       |
| `KWARGS`              | A binding fragment's constructor keywords disagree with the manifest, so the `.pyi` advertises a signature the compiled object rejects (gh-612, gh-823). jm regenerates a kwlist only with the body it belongs to: reconcile the manifest with the binding, or move the hand-written constructor into an `_extra.c`. Suppressible per file with `status_allow`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | yes       |
| `RECORDS`             | A record's fields disagree with the manifest: the `.pyi` documents a field the extension does not have, so `.field` raises `AttributeError` on a name a type checker accepts (gh-1290). Delete the record's wrapper and its row and re-run `jm apply`, or keep the binding in an `_extra.c`. Suppressible with `status_allow`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | yes       |
| `UNEXPLAINED OPT-OUT` | A `[module.X] no_generate` with no `no_generate_reason`, or a reason left on a module that generates again (gh-1313). Never suppressible: the fix is one line of TOML.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | yes       |
| `UNRECONCILED`        | Files `jm apply` reconciles in place but not wholesale (gh-848) — a binding fragment holding a hand-written body, say — each listed with why it differs.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | no        |
| `STALE ALLOW`         | A `status_allow` entry that matches no managed file. A renamed or deleted component leaves its pattern behind, and it would then silence whatever path later matches it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | no        |
| `PACKAGING`           | A packaging template without the `# jm:generated` line that is behind today's render (gh-1589). `jm adopt --packaging` hands it to jm — see [`adopt --packaging`](#just-makeit-adopt-packaging).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | no        |
| `PKG-CONFIG`          | A `[project] find_packages` entry the installed `.pc` cannot name (gh-1576). Give the entry a `pkg_config` module, or `libs_private` and `cflags` when the dependency ships no `.pc`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | no        |
| `BACKINGS`            | Under a `c_prefix`, how each capsule / composer `backing` spells its C API: through a component's symbols, or exactly as written (gh-1685). Not printed under `--check`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | no        |
| `TOKEN WITHOUT KEY`   | A binding fragment that says jm regenerates it while its object no longer declares `fragment = "generated"`. Restore the key, or delete the file and re-run `jm apply`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | no        |
| `EXAMPLE`             | A header's `@code` example calls `create()` with a different number of arguments than the declaration (gh-1502). The example is your text, so jm reports it and rewrites nothing.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | no        |
| `SUPERSEDED`          | A file jm has renamed, still on disk under its old name (gh-1472). `apply` holds the new one back rather than create a default beside it; the listing says what to do.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | no        |

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
  . nco.steps_u32: <pkg>_nco_steps_u32_max_out(state) cannot see the call,
      so the allocation stays clamped to max(max_out, n).
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

"A build file" is every `CMakeLists.txt` in the project (not a build tree or
`vendor/`), the `<dir>_extra.cmake` hook each one includes, and the root
`Makefile` / `local.mk`. So a hand-written test or benchmark wired in the
[hook jm tells you to use](../customization.md)
counts as built (gh-1432); before, it was reported `UNBUILT` while it compiled.

`UNBUILT` reports a benchmark orphan only when the tree builds **some**
benchmark. A project that builds none — one predating the `make` backend's
`bench:` target (gh-832) — has the whole category unbuilt by construction, and
failing the gate for something no `jm apply` can clear is the thing this
deliberately does not do. It arms itself the moment the project gains bench
rules.

### Why `CTOR` gates, and why it reads the header rather than the `_core.c`

Every file jm owns renders from the same `init_params`, so they agree with each
other **by construction** — the stub, the binding and the aggregator cannot
disagree about a constructor, because one list produces all three. The only
file that can disagree is the hand-written one, and `_core.c` is sacred: jm
never reads it.

So `jm status --check` could report `OK — up to date` over a manifest declaring
one `float[]` parameter and a C constructor taking `(size_t num_taps, const float *h)`. doppler carried exactly that, and it was found by reviewing an
unrelated jm change rather than by any gate.

**The gap was not uniform, which is why it survived.** Reordering a
hand-written `create()`'s parameters on a *standalone* object is caught — its
`_core.h` is a manifest-owned file, so the whole-file diff reports `STALE`. The
same file with the same edit on a *module* object reported `OK`. One edit,
caught in one layout and invisible in the other.

`CTOR` compares one thing — the injected declaration against the rendered
parameter list — so it answers the same way in both layouts and does not depend
on the whole-file diff.

**It reads `_core.h`, not `_core.c`, and that is the point.** A definition that
disagrees with its own header will not compile, so checking the header puts the
definition under the compiler's gate for free — no C parsing, and no reading of
a sacred file.

It is never suppressible, on the same rule as the gh-442 `DRIFT` beside it: jm
cannot know whether you meant to change the C or forgot to change the manifest,
and while the two disagree the project cannot be regenerated. There is no
reading of `status_allow` under which that is intended.

### Why a stand-down is `UNCHECKED` and not silence

The scan reads build files as text, and one shape defeats that outright: a
`file(GLOB ...)` compiles whatever matches, so no amount of reading answers
"is this source built?". jm stands down rather than guess, because a wrong
"unbuilt" sends someone to delete a file that *is* compiled.

Standing down used to look exactly like finding nothing — an empty listing and
an `OK` line (gh-1033). That is the gh-806 failure one layer out: a check whose
whole purpose is to break a silence, reporting a tree it never read as clean.
So the stand-down is now printed as `UNCHECKED`, the `OK` line carries
`unbuilt not checked`, and `--json` carries `"unbuilt_scanned": false`.

It does not gate. There is no command that clears it — `jm apply` cannot
rewrite your wildcard — and gh-767's rule is that a gate must name a fix. What
puts the tree back under the gate is naming the sources explicitly in the build
file.

`jm bench`'s discovery makes the same stand-down and has printed the same kind
of note since gh-1023; the two now agree.

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

## `just-makeit adopt`

Make a module object's binding fragment (`<mod>_ext_<obj>.c`) jm's content,
so every future binding fix reaches it on `apply`. Writes
`fragment = "generated"` only where nothing of yours would be lost. Why and
when: [Who owns a module's binding fragment](../configuration.md#who-owns-a-modules-binding-fragment).

```sh
just-makeit adopt --check                  # what each object would do; writes nothing
just-makeit adopt fir                      # flip one object (and its views)
just-makeit adopt --module dsp             # every object in a module
just-makeit adopt fir --accept-additions   # also take units the render only adds to
```

| Flag                               | Description                                                                                                                                                             |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--check`                          | Report per object — `would flip`, `needs acknowledgement`, `REFUSES` — and write nothing. Exits non-zero when anything cannot flip unattended.                          |
| `<obj>…` / `--module ID` / `--all` | The objects to flip. There is no default: without one, `adopt` writes nothing.                                                                                          |
| `--accept UNIT`                    | Take one differing unit (`fn:Fir_init`, `table:PyMethodDef`, as `--check` prints them) by name. Repeatable. A name that matches no differing unit is refused as a typo. |
| `--accept-additions`               | Take every unit `--check` labels `adds only`: the render keeps every token of it and adds code, so nothing on disk is lost.                                             |

An object flips only when nothing refuses it and every differing unit is
accepted; otherwise nothing is written for it. A unit that exists only on
disk always refuses — move it to the `_extra.c` beside the fragment.

### `just-makeit adopt --packaging`

Make the packaging files jm's, so `apply` renders them and every pkg-config
and `find_package` fix reaches the project (gh-1589): the templates
`cmake/<pkg>.pc.in` and `cmake/<pkg>-config.cmake.in`, and the root
`CMakeLists.txt`'s install section. A project scaffolded since gh-1589 has
them born owned -- each template's first line is `# jm:generated <file>`, and
the install section is the managed block from `# ── Install` to
`# ── End install`. An older one has neither: `jm status` names each template
that is behind under **PACKAGING**, and the section as
`ROOT CMAKE install-block`.

```sh
just-makeit adopt --packaging --check                       # diffs, and what would be dropped; writes nothing
just-makeit adopt --packaging                               # take every template that loses nothing
just-makeit adopt --packaging --accept my_proj-config.cmake.in   # take one anyway
```

| Flag            | Description                                                                                                                                                    |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--packaging`   | Act on the packaging templates. Takes no objects, `--module`, `--all` or `--accept-additions`.                                                                 |
| `--check`       | Print each template's verdict, its diff against today's render, and every line adopting would drop. Writes nothing; exits non-zero when a template is refused. |
| `--accept PATH` | Take a refused template (by path `cmake/p.pc.in` or file name) or install section (`CMakeLists.txt`) anyway. Repeatable.                                       |

A line on disk that the render does not keep refuses its template. An older
jm's line that today's render only extends (`Cflags: -I${includedir}` →
`Cflags: -I${includedir}@JM_PC_CFLAGS@`) is kept; one jm has since respelled
cannot be told from a line you wrote, so it is listed and `--accept` takes it
after you have read the diff. To keep something of yours, set it as a CMake
variable in the root `CMakeLists.txt` that the template reads. Deleting the
`# jm:generated` line afterwards hands a template back to you for good.

The install section is judged by COMMAND, the same way: adopting replaces it,
from the `# ── Install` line to the end of the file, with the managed block,
and refuses when that would drop a command no released jm rendered there --
doppler's `include(cmake/packaging.cmake)`, say. Move such a command above
the `# ── Install` line and adopt; afterwards your own install rules go below
`# ── End install`, which jm never writes. Inside the block, `apply` compares
commands, not bytes, so your formatter's layout is never rewritten.

## `just-makeit script`

Print a shell script to stdout that fully reconstructs the current project
from scratch via CLI commands. Must be run from the project root.

```sh
just-makeit script              # print to stdout
just-makeit script > rebuild.sh # save to file
```

Reads `just-makeit.toml` and emits one command per scaffold step in the
correct category order: `new` → `module` → `object` → `record` → `method` →
`property` → `view` → `warning` → `error` → `function`, plus
`config version` when the version is not `0.1.0`. A key with no CLI spelling
is listed as a `# NOTE:` comment saying where to re-add it. The output is a
valid shell script that, when run from the parent directory, produces an
identical `just-makeit.toml`. Within the `object` category, order matches
original creation order for a `--no-fragments` (single-manifest) project;
under the default fragment layout, objects are read back from
`objects/*.toml` and so are reconstructed in filename (alphabetical) order
instead — harmless for correctness (each `object` command is independent) but
the sequence may not match how you originally typed it.

**Note:** `--impl` / `--replace` are not stored in `just-makeit.toml` (the
lifted body is patched directly into the generated files), so they are not
reproduced. Implemented function and step bodies are preserved in your C
source files and are unaffected.

**Example**

Given a project with two objects (abridged; `jm new` gives every project a
`c_prefix`, and the default layout keeps each object in its own fragment):

```toml
# just-makeit.toml
include = ["objects/*.toml", "modules/*.toml"]

[project]
name = "dsp_toolkit"
version = "0.1.0"
build = "cmake"
c_prefix = "dsp_toolkit"

# objects/gain.toml
[gain]
arg_type = "float"
return_type = "float"

[[gain.state]]
name = "gain"
type = "float"
default = "1.0"

# objects/ema.toml
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

just-makeit new dsp_toolkit \
    --c-prefix dsp_toolkit
cd dsp_toolkit

just-makeit object ema \
    --state alpha:double:0.1 \
    --state prev:float:0.0 \
    --arg-type float
just-makeit object gain \
    --state gain:float:1.0 \
    --arg-type float
```

Running that script from the parent directory recreates the project structure
and an identical `just-makeit.toml`.

______________________________________________________________________

## `just-makeit migrate-to-fragments`

Move every `[<obj>]` section of `just-makeit.toml` into `objects/<obj>.toml`
and every `[module.X]` into `modules/<name>.toml`, leaving the manifest with
`[project]` and the `include` globs — the layout `jm new` gives a project
today. The merged manifest is unchanged, and running it again is a no-op. Must
be run from the project root.

Fragments load sorted by file name, so the order the manifest lists
components in can change. The root `CMakeLists.txt` wiring and the umbrella
header's includes are re-sorted to match — the order `jm apply` writes — and
nothing in them is added or removed.

```sh
just-makeit migrate-to-fragments
```

See [Migrating an existing
project](../declarative-scaffolding.md#migrating-an-existing-project).

______________________________________________________________________

## `just-makeit split-objects`

The objects-only subset of `migrate-to-fragments`: each `[<obj>]` section
moves to `objects/<obj>.toml` and every `[module.X]` stays inline. Prefer
`migrate-to-fragments` unless you want the modules to stay put. It re-sorts
the root `CMakeLists.txt` wiring and the umbrella header the same way.

```sh
just-makeit split-objects
```

______________________________________________________________________

## `just-makeit upgrade`

Migrate an older project's `just-makeit.toml` to the current schema and apply
the repairs that go with it. See [Upgrading an existing
project](../upgrading.md).

______________________________________________________________________

## `just-makeit example [name]`

Run a bundled example end to end — scaffold, implement, build, test — in a
temporary directory, printing its output as it goes. Omit the name to list the
examples. See [Examples](../examples/index.md).

```sh
just-makeit example              # list the bundled examples
just-makeit example fir_filter   # run one
```

______________________________________________________________________

## `just-makeit install-deps [path]`

Install the build dependencies: cmake, a C compiler, pkg-config and, on Linux,
patchelf through the system package manager when one of them is missing, then
numpy and just-makeit into a Python venv at `path` (default `/tmp/jm-venv`).
It prints the `source <path>/bin/activate` line to run. See
[Get it](../index.md#get-it).

```sh
just-makeit install-deps             # venv at /tmp/jm-venv
just-makeit install-deps ~/my-venv   # venv elsewhere
just-makeit install-deps --check     # report what is missing; install nothing
```

| Flag         | Description                                                          |
| ------------ | -------------------------------------------------------------------- |
| `--check`    | Report what is installed and missing; exit 1 if anything is missing. |
| `-h, --help` | Show the full reference: what it installs, the environment it reads. |

______________________________________________________________________

## `just-makeit version`

Print just-makeit's version. `just-makeit --version` and `just-makeit -V` are
the same; `just-makeit help` (or `--help`) prints the command reference.

```sh
just-makeit version
```

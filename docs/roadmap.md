# Roadmap

## Why just-makeit exists

You have an algorithm. A filter, a detector, a codec — working C, or a clear
idea of the math. You want it in Python: fast, importable, tested, with a
clean C ABI that C++, Rust, or anything else can link against too.

Almost none of the distance between those two points is your algorithm. It's
plumbing — CMake wrangling, CPython C-API spelunking, the NumPy buffer
protocol, type stubs, a test harness, a build matrix, packaging. Every bit of
it stands between you and shipping, and none of it is the thing you actually
set out to build.

just-makeit deletes that distance. You describe the shape once — or point it
at your C — and everything that isn't your algorithm is generated and kept in
sync: the binding, the build, the tests, the benchmarks, the type stubs, the
distributable C library. **The scaffolding disappears. Your algorithm is all
that remains.**

Two principles keep that honest as the tool grows:

- **Your code is sacred.** Generated glue regenerates from the manifest; the
    file your algorithm lives in is never overwritten. Editing a project is
    safe and predictable, not a gamble. (This is the v0.14 sacred/glue
    lifecycle.)
- **No foot-guns.** Every valid command produces a scaffold that compiles and
    passes its tests on day one. If it doesn't, that's our bug — not your
    mistake.

______________________________________________________________________

## Where we are

just-makeit scaffolds, builds, tests, benchmarks, and distributes a Python C
extension today — and the same C compiles once into a standalone library for
C, C++, and Rust. The full I/O matrix (scalar, array, void, real, complex),
multi-type modules, shape presets, performance scaffolding (portable SIMD
macros), and the sacred/glue edit lifecycle are all shipped.

The version-by-version history lives in
[CHANGELOG.md](../CHANGELOG.md). This page is only about what's ahead.

The question for what's next isn't "what features are missing." It's **"where
does the plumbing still show?"**

______________________________________________________________________

## Where we're headed

Three horizons. The near-term work is concrete and close; the later it gets,
the more a line is a direction than a date. If one of these is something you
hit, we'd like to hear about it.

### Now — bank the easy wins and make good on the promises

The fastest value is the work that's nearly done and the promises already
made out loud.

- **Land what's already built.** `jm status` (a read-only report of where the
    files have drifted from the manifest) and auto-fetching a prebuilt
    external dependency for the example tests are done — they just need to
    ship.
- **`jm ci`.** Generate the GitHub Actions / Woodpecker workflow, so a
    scaffolded project is CI-green as fast as it builds and tests locally.
    This is the last gap between "works on my machine in a minute" and "works
    in CI."
- **Keep the no-foot-guns net tight.** Every valid command stays green on day
    one; every new shape gets a regression test before it ships. This is a
    standing promise, not a milestone.
- **Make "your code is sacred" whole.** The sacred/glue lifecycle protects
    your algorithm in place; the next step makes a whole *component* portable
    — its spec as a per-component `objects/NAME.toml` / `modules/NAME.toml`
    fragment, per-function `.c` files in modules, and `jm migrate-to-fragments`
    for existing projects. Copying a component becomes "copy its fragment and
    source dirs, run `jm apply`."

### Next — write it in C, get Python (`jm bind`)

Many people already have the C. For them the fastest path isn't a manifest at
all — it's "point at my header and give me the Python." `jm bind` reads a
hand-written `_core.h` and synthesises the binding; today it handles the
simple processor shape. The work is to make it handle everything else:

- Parse every shape — methods, constructor params, opaque state,
    variable-output, result-structs — not just scalar-in/scalar-out.
- `jm bind --check` as a CI gate, so a binding can never silently drift from
    the header it came from.
- Bind a real third-party single-header library unchanged; fall back to
    libclang when the fast regex parser can't keep up.

This is the line between "a scaffolding tool" and "the bridge from your C to
every language." It's the most likely headline for 1.0.

### Later — breadth and edges

- **Windows parity** — the MinGW path works but has rough edges (symbol
    visibility, DLL loading, path handling); close the gap with docs, CI
    coverage, and a few generated-file fixes.
- **`--ufunc`** — expose the step function as a NumPy generalized ufunc:
    broadcasting and `out=` for filter-bank and vectorized-pipeline use.

______________________________________________________________________

## How to read this page

A line here is an intention, not a commitment. Priorities follow the problems
people actually run into — so the surest way to move something up is to open
an issue describing where the plumbing got in your way. For what has already
shipped, and exactly when, see [CHANGELOG.md](../CHANGELOG.md).

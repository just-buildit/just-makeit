# Glossary

Definitions for terms used throughout these docs.

______________________________________________________________________

**OBJECT library**
A CMake library type (`add_library(foo OBJECT ...)`) that compiles source files
to object files without linking them into an archive or shared library. Each
component in a just-makeit project is an OBJECT library so its compiled code
can be linked into *both* the Python extension and the combined C shared
library without being compiled twice.

______________________________________________________________________

**dispatch loop**
The outer loop that drives a DSP object over a block of samples — calling
`step()` once per sample and managing any scratch buffer or SIMD stride. In
generated projects this is `<obj>_steps()` (hand-written) or the body produced
by `JM_DEFINE_STEPS` (macro-generated).

______________________________________________________________________

**stateful object**
An object with persistent state between calls: delay lines, coefficient arrays,
running accumulators. The default `just-makeit object` shape. State lives in
a heap-allocated `<obj>_state_t` struct; the Python type holds a pointer to it.
See [Stateful vs Pure](pure.md).

______________________________________________________________________

**pure function**
An object shape with no persistent state. Every call is independent; the C
function takes only its input parameters and returns a result. Scaffolded with
`--no-state` or other flags that suppress state generation.

______________________________________________________________________

**step function**
The per-sample inner function — `<obj>_step(state, x)` — implemented by the
user in `<obj>_core.h` as a `static inline` for maximum inlining opportunity.
`step()` is the algorithm; the dispatch loop calls it.

______________________________________________________________________

**module subpackage**
A Python extension module that groups multiple types into one `.so` file and
one subpackage import (`from my_pkg.filter import Fir, Biquad`). Created with
`just-makeit module`; types added with `just-makeit object --module`.

______________________________________________________________________

**standalone object**
A Python type with its own `.so` file and top-level package import
(`from my_pkg import Gain`). Created with `just-makeit object` (no `--module`).

______________________________________________________________________

**extension module**
The Python C extension — the compiled `.so` or `.pyd` file that CPython loads
when you `import`. In standalone mode each object has its own extension module.
In module mode multiple types share one.

______________________________________________________________________

**perf tier**
The SIMD instruction set selected when building with `-DENABLE_SIMD=ON`.
`jm_simd.h` supports three tiers: AVX-512F (16 floats/cycle), AVX2+FMA (8
floats/cycle), and scalar (1 float/cycle, compiler-autovectorisable). The tier
is detected at compile time; no runtime dispatch.

______________________________________________________________________

**property**
A Python accessor pair (`get_<name>` / `set_<name>`) added to a generated type
with `just-makeit property`. Can be backed by a struct field (`--field`, auto-
implemented) or implemented manually in the C extension.

______________________________________________________________________

**method**
An arbitrary C function exposed as a Python method on a generated type,
added with `just-makeit method`. Supports scalar and array parameters, and
void or scalar return types.

______________________________________________________________________

**`just-makeit.toml`**
The project manifest — the source of truth for all subsequent commands.
Records the project name, version, all declared objects and modules, and every
flag (`--perf`, `--arg-type`, `--return-type`, etc.) so that `just-makeit add`
and `just-makeit apply` regenerate files consistently with the original
scaffold.

______________________________________________________________________

**glue file**
A generated file that is rebuilt from the manifest on every `just-makeit apply`:
`<comp>_ext.c` (CPython binding), `<comp>.pyi` (type stub), and `CMakeLists.txt`.
Editing the TOML propagates straight into the glue. `<comp>_core.h` is
*mixed*: `apply` injects a missing method/property declaration, but the inline
`step()` body and the state struct are sacred — never re-rendered (a new state
field reaches the struct via a rebuild: `jm add` / `jm regenerate`).

______________________________________________________________________

**sacred file**
A generated file that apply never overwrites once it exists — `<comp>_core.c`
(your `steps()` and lifecycle bodies) and the generated tests. To intentionally
rebuild a sacred file from the manifest, use `just-makeit regenerate <comp>`
(`git stash` first, since it discards hand-written bodies).

# Glossary

Definitions for terms used throughout these docs.

______________________________________________________________________

**OBJECT library**
A CMake library type (`add_library(foo OBJECT ...)`) that compiles source files
to object files without linking them into an archive or shared library. Each
component in a just-makeit project is an OBJECT library (an INTERFACE library
under `--header-only`) so its compiled code can be linked into *both* the
Python extension and the combined C shared library without being compiled
twice.

______________________________________________________________________

**C symbol stem**
`<c_prefix>_<comp>` — the prefix of every C identifier jm derives for a
component (`<pkg>_<comp>_create`, `<pkg>_<comp>_state_t`, …). `jm new <pkg>`
sets `c_prefix` to the package name, so the stem is `<pkg>_<comp>` (or just
`<comp>` when the name already starts `<pkg>_`); `jm new --no-c-prefix` leaves
it bare (`<comp>`). File names, CMake targets and Python names keep the plain
component name.

______________________________________________________________________

**dispatch loop**
The outer loop that drives a DSP object over a block of samples — calling
`step()` once per sample and managing any scratch buffer or SIMD stride. In
generated projects this is `<pkg>_<comp>_steps()` (scaffolded, yours to edit)
or the body produced by `JM_DEFINE_STEPS` (macro-generated).

______________________________________________________________________

**stateful object**
An object with persistent state between calls: delay lines, coefficient arrays,
running accumulators. The default `just-makeit object` shape. State lives in
a heap-allocated `<pkg>_<comp>_state_t` struct; the Python type holds a pointer
to it. See [Stateful vs Pure](pure.md).

______________________________________________________________________

**pure function**
A C function with no persistent state, exposed as a module-level Python
function with `just-makeit function NAME --module M`. Every call is
independent; the C function takes only its input parameters and returns a
result. (`just-makeit object --no-state` is different: it scaffolds an *empty*
state struct you fill yourself — see [Stateful vs Pure](pure.md).)

______________________________________________________________________

**step function**
The per-sample inner function — `<pkg>_<comp>_step(state, x)` — implemented by
the user in `<comp>_core.h` as a `static inline` for maximum inlining
opportunity. `step()` is the algorithm; the dispatch loop calls it.

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
`jm_simd.h` supports four tiers: AVX-512F (16 float lanes), AVX2+FMA (8 float
lanes), NEON on aarch64 (4 float lanes — always active there, since NEON is
part of the mandatory ARMv8-A baseline rather than an opt-in flag like
AVX2/AVX-512), and scalar (1 lane, compiler-autovectorisable). The tier is
detected at compile time; no runtime dispatch.

______________________________________________________________________

**property**
A Python attribute (`obj.name`; read-only unless `--writable`) added to a
generated type with `just-makeit property`. By default it is backed by C
accessors `<pkg>_<comp>_get_<name>` / `<pkg>_<comp>_set_<name>` that you
implement in `<comp>_core.c`; it can instead be backed by a struct field
(`--field`, auto-implemented), a C expression (`--expr`), a buffer view
(`--buf-field`), an `[[enum]]` (`--enum`), or a container (`--value-type`).

______________________________________________________________________

**method**
An arbitrary C function exposed as a Python method on a generated type,
added with `just-makeit method`. Parameters may be scalars, arrays or writable
`--out-param` buffers; results may be a scalar, `None`, a per-call or
variable-length array (`--out-type`, `--variable-output`, `--multi-output`),
records (`--result-field`, `--single`, `--record-dtype`) or a zero-copy view
(`--borrow`). See [Extend commands](commands/extend.md#just-makeit-method).

______________________________________________________________________

**`just-makeit.toml`**
The project manifest — the source of truth for all subsequent commands.
`just-makeit.toml` holds `[project]` (name, version, `c_prefix`, schema) and
top-level tables such as `[[enum]]`; each object's and module's sections live
in the `objects/<name>.toml` / `modules/<name>.toml` fragments it includes
(`jm new --no-fragments` keeps everything in one file). Every flag (`--perf`,
`--arg-type`, `--return-type`, etc.) is recorded so that `just-makeit add` and
`just-makeit apply` regenerate files consistently with the original scaffold.

______________________________________________________________________

**glue file**
A generated file that is rebuilt from the manifest on every `just-makeit apply`:
`<comp>_ext.c` (CPython binding), `<comp>.pyi` (type stub), the umbrella
`<pkg>.h`, and the component `CMakeLists.txt` (reconciled, keeping your riders;
your own CMake goes in `<comp>_extra.cmake`). Editing the TOML propagates
straight into the glue. `<comp>_core.h` is *mixed*: `apply` injects a missing
method/property declaration, but the inline `step()` body and the state struct
are sacred — never re-rendered (a new state field reaches the struct via a
rebuild: `jm add` / `jm regenerate`).

______________________________________________________________________

**sacred file**
A generated file that apply never overwrites once it exists — `<comp>_core.c`
(your `steps()` and lifecycle bodies) and the generated tests. To intentionally
rebuild a sacred file from the manifest, use `just-makeit regenerate <comp>`
— by default it lifts hand-written bodies and splices them back into the
fresh scaffold, so `git stash` first regardless (the splice is best-effort);
pass `--discard` for a clean reset that drops them for good.

______________________________________________________________________

**bind**
The `jm bind` command reads a hand-written `<comp>_core.h` and synthesises the
CPython binding (`<comp>_ext.c` and `<comp>.pyi`) from it — the "point at your
C, get Python" path, for a component the manifest does not declare (a declared
one's binding is `jm apply`'s, and `bind` refuses it). It handles scalar or
opaque state, constructor params, getter/setter properties, single-argument
custom verbs and variable-output methods; see
[`just-makeit bind`](commands/build.md#just-makeit-bind-component)
for usage and `--check` (CI gate). Multi-parameter methods, result structs and
a libclang fallback are the remaining work on the
[roadmap](roadmap.md#now-write-it-in-c-get-python-jm-bind).

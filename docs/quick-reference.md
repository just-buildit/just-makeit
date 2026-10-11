# Quick reference — Python → TOML → CLI

Each row shows an annotated Python stub, the manifest TOML that produces it,
and the CLI command that writes that TOML. An object's tables live in
`objects/<comp>.toml` and a module's in `modules/<mod>.toml` (the default
layout; `jm new --no-fragments` keeps everything in `just-makeit.toml`);
top-level `[[enum]]` tables go in `just-makeit.toml`. *TOML only* means the
feature is not reachable from the CLI and must be written by hand, then
applied with `jm apply` (which regenerates the glue files from the manifest).

______________________________________________________________________

## Object shapes

<table>
<thead>
<tr>
<th>Python stub</th>
<th>TOML</th>
<th>CLI</th>
</tr>
</thead>
<tbody>

<tr>
<td>

```python
# Scalar in → scalar out
def step(
    self, x: float
) -> float: ...
```

</td>
<td>

```toml
[comp]
arg_type    = "float"
return_type = "float"
```

</td>
<td>

```sh
jm object comp \
  --arg-type \
    float \
  --return-type \
    float
```

</td>
</tr>

<tr>
<td>

```python
# Generator — no input
def step(self) -> complex: ...
```

</td>
<td>

```toml
[comp]
arg_type    = "void"
return_type = "float _Complex"
mutable     = "true"
```

</td>
<td>

```sh
jm object comp \
  --arg-type void \
  --return-type \
    "float _Complex" \
  --mutable
```

</td>
</tr>

<tr>
<td>

```python
# Sink — no output
def step(
    self, x: float
) -> None: ...
```

</td>
<td>

```toml
[comp]
arg_type    = "float"
return_type = "void"
```

</td>
<td>

```sh
jm object comp \
  --arg-type \
    float \
  --return-type \
    void
```

</td>
</tr>

<tr>
<td>

```python
# Buffer step (blockwise)
def steps(
    self,
    x: npt.NDArray[np.complex64],
    out: npt.NDArray[np.complex64]
         | None = None,
) -> NDArray[np.complex64]: ...
```

</td>
<td>

```toml
[comp]
arg_type    = "float _Complex[]"
return_type = "float _Complex[]"
```

</td>
<td>

```sh
jm object comp \
  --preset blockwise
```

</td>
</tr>

<tr>
<td>

```python
# Stateful constructor
class Gain:
    def __init__(
        self,
        gain: float = ...,
    ) -> None: ...
```

</td>
<td>

```toml
[[gain.state]]
name    = "gain"
type    = "double"
default = "1.0"
```

</td>
<td>

```sh
jm object gain \
  --state \
    gain:double:1.0
```

</td>
</tr>

<tr>
<td>

```python
# Custom class name
class NCO: ...
```

</td>
<td>

```toml
[nco]
class_name = "NCO"
```

</td>
<td>

```sh
jm object nco \
  --class-name NCO
```

</td>
</tr>

</tbody>
</table>

______________________________________________________________________

## Constructor parameters (`--init-param`)

<table>
<thead>
<tr>
<th>Python stub</th>
<th>TOML</th>
<th>CLI</th>
</tr>
</thead>
<tbody>

<tr>
<td>

```python
# Scalar with default
def __init__(
    self,
    order: int = 4,
) -> None: ...
```

</td>
<td>

```toml
[comp]
no_state = "true"

[[comp.init_params]]
name    = "order"
type    = "int"
default = "4"
```

</td>
<td>

```sh
jm object comp \
  --no-state \
  --init-param order:int:4
```

</td>
</tr>

<tr>
<td>

```python
# Required array
def __init__(
    self,
    coeff: npt.NDArray[np.complex64],
) -> None: ...
```

</td>
<td>

```toml
[[comp.init_params]]
name = "coeff"
type = "float _Complex[]"
```

</td>
<td>

```sh
jm object comp --no-state \
  --init-param \
  "coeff:float _Complex[]"
```

</td>
</tr>

<tr>
<td>

```python
# Defaulted (optional) 1-D array
# empty array when omitted
def __init__(
    self,
    template: npt.NDArray[np.float32]
             = ...,
    pfa: float = ...,
) -> None: ...
```

</td>
<td>

```toml
[[comp.init_params]]
name    = "template"
type    = "float[]"
default = "[]"

[[comp.init_params]]
name    = "pfa"
type    = "float"
default = "0.1"
```

</td>
<td>

```sh
jm object comp --no-state \
  --init-param \
    "template:float[]:[]" \
  --init-param pfa:float:0.1
```

**The way to make an
array omittable.**
Omit it (or pass `None`) and
`create()` receives `NULL`
with length `0`. Any number
of arrays compose: they
share ONE `create()` call,
one `NULL`/`0` pair each.
gh-611 also gives it its
declared position among the
optional params instead of
hoisting it first, unlike a
required array.

Not `optional` — that is
array *dispatch* (a second
`create_fn`), one array
only.

</td>
</tr>

<tr>
<td>

```python
# Optional 2-D array
def __init__(
    self,
    bank: npt.NDArray[np.float32]
         | None = None,
    rate: float = ...,
) -> None: ...
# bank → fir_create_poly(d0,d1,ptr,rate)
# None → <pkg>_fir_create(rate)
```

</td>
<td>

```toml
[[comp.init_params]]
name      = "bank"
type      = "float[][]"
optional  = true
create_fn = "fir_create_poly"
```

</td>
<td>

```sh
jm object comp --no-state \
  --init-param \
    "bank:float[][]:optional:fir_create_poly"
```

</td>
</tr>

<tr>
<td>

```python
# String-enum choice
from typing import Literal
def __init__(
    self,
    mode: Literal["fast", "hq"]
        = "fast",
) -> None: ...
```

</td>
<td>

```toml
[[comp.init_params]]
name    = "mode"
type    = "string_enum:fast,hq"
default = "fast"
```

</td>
<td>

```sh
jm object comp \
  --init-param \
    "mode:string_enum:fast,hq:fast"
```

</td>
</tr>

<tr>
<td>

```python
# Dtype-dispatched array
# int16 ndarray → real_create_fn
# other → <pkg>_<comp>_create
```

</td>
<td>

```toml
[[comp.init_params]]
name           = "buf"
type           = "float[]"
real_type      = "int16_t"
real_create_fn = "comp_create_i16"
```

</td>
<td>

*TOML only*

</td>
</tr>

</tbody>
</table>

______________________________________________________________________

## Methods and functions

<table>
<thead>
<tr>
<th>Python stub</th>
<th>TOML</th>
<th>CLI</th>
</tr>
</thead>
<tbody>

<tr>
<td>

```python
# Named method
def execute_ctrl(
    self, x: float
) -> float: ...
```

</td>
<td>

```toml
[[comp.methods]]
name        = "execute_ctrl"
arg_type    = "float"
return_type = "float"
```

</td>
<td>

```sh
jm method comp execute_ctrl \
  --arg-type \
    float \
  --return-type \
    float
```

</td>
</tr>

<tr>
<td>

```python
# Variable-length output
def execute(
    self,
    x: npt.NDArray[np.complex64],
    out: npt.NDArray[np.complex64]
         | None = None,
) -> NDArray[np.complex64]: ...
```

</td>
<td>

```toml
[[comp.methods]]
name            = "execute"
arg_type        = "float _Complex"
variable_output = true
```

</td>
<td>

```sh
jm method comp execute \
  --arg-type \
    "float _Complex" \
  --variable-output
```

</td>
</tr>

<tr>
<td>

```python
# Dual output
def execute(
    self,
    x: npt.NDArray[np.uint32],
) -> tuple[
    NDArray[np.uint32],
    NDArray[np.uint8],
]: ...
```

</td>
<td>

```toml
[[comp.methods]]
name            = "execute"
arg_type        = "uint32_t"
return_type     = "uint32_t"
variable_output = true
multi_output    = ["uint8_t"]
```

</td>
<td>

```sh
jm method comp execute \
  --arg-type uint32_t \
  --return-type uint32_t \
  --variable-output \
  --multi-output uint8_t
```

</td>
</tr>

<tr>
<td>

```python
# Struct-list return
def find_peaks(
    self,
    x: npt.NDArray[np.float32],
) -> list[tuple[int, float]]: ...
```

</td>
<td>

```toml
[[comp.methods]]
name        = "find_peaks"
arg_type    = "float[]"
# your struct, in the sacred _core.h
return_type = "comp_peak_t"
max_results = 64

[[comp.methods.result_fields]]
name = "index"
type = "size_t"

[[comp.methods.result_fields]]
name = "magnitude"
type = "float"
```

</td>
<td>

```sh
jm method comp find_peaks \
  --arg-type "float[]" \
  --return-type comp_peak_t \
  --result-field index:size_t \
  --result-field \
    magnitude:float
```

</td>
</tr>

<tr>
<td>

```python
# Read-only property
@property
def length(self) -> int: ...
```

</td>
<td>

```toml
[[comp.properties]]
name = "length"
type = "int"
```

</td>
<td>

```sh
jm property comp length \
  --type \
    int
```

</td>
</tr>

<tr>
<td>

```python
# Writable property
@property
def gain(self) -> float: ...
@gain.setter
def gain(
    self, value: float
) -> None: ...
```

</td>
<td>

```toml
[[comp.properties]]
name     = "gain"
type     = "double"
writable = true
```

</td>
<td>

```sh
jm property comp gain \
  --type \
    double \
  --writable
```

</td>
</tr>

<tr>
<td>

```python
# Warn after construction
# when a state flag is set
import warnings
with warnings.catch_warnings():
    Comp()  # -> UserWarning
```

</td>
<td>

```toml
[[comp.warnings]]
after     = "__init__"
condition = "underpowered"
category  = "UserWarning"
message   = "best effort only"
```

</td>
<td>

```sh
jm warning comp \
  --condition \
    underpowered \
  --message \
    "best effort only"
```

</td>
</tr>

<tr>
<td>

```python
# create() refusal reports
# the real reason, not
# a blanket MemoryError
Comp(reps=0)
# -> ValueError: bad params
```

</td>
<td>

```toml
[comp]
create_error = "ValueError"
create_error_message = \
  "bad params"
```

</td>
<td>

```sh
jm error comp \
  --category \
    ValueError \
  --message \
    "bad params"
```

</td>
</tr>

<tr>
<td>

```python
# Teardown is named, and
# can fail. A failing
# close raises OUT of
# the with block.
with Writer(p) as w:
    w.write(x)
# -> OSError: ...
w.destroy()  # alias
```

</td>
<td>

```toml
[comp.destroy]
name    = "close"
aliases = ["destroy"]
returns = "int"
error   = "OSError"
error_message = \
  "close failed"
```

</td>
<td>

```text
manifest only —
no CLI flag; edit
the TOML and run
`jm apply`
```

</td>
</tr>

<tr>
<td>

```python
# Module-level function
def apply(
    x: npt.NDArray[np.float32],
    scale: float,
) -> float: ...
```

</td>
<td>

```toml
[[module.dsp.functions]]
name        = "apply"
return_type = "float"

[[module.dsp.functions.params]]
name = "x"
type = "float[]"

[[module.dsp.functions.params]]
name = "scale"
type = "float"
```

</td>
<td>

```sh
jm function apply \
  --module dsp \
  --param "x:float[]" \
  --param "scale:float" \
  --return-type \
    float
```

</td>
</tr>

<tr>
<td>

```python
# Inline function (header-only)
# same signature; compiler
# can inline at call sites
```

</td>
<td>

```toml
[[module.dsp.functions]]
name   = "apply"
inline = true
```

</td>
<td>

```sh
jm function apply \
  --module dsp \
  --inline ...
```

</td>
</tr>

<tr>
<td>

```python
# Function with array output
def magnitude_db(
    x: npt.NDArray[np.complex64],
    floor: float,
) -> NDArray[np.float32]: ...
```

</td>
<td>

```toml
[[module.dsp.functions]]
name     = "magnitude_db"
out_type = "float"

[[module.dsp.functions.params]]
name = "x"
type = "float _Complex[]"

[[module.dsp.functions.params]]
name = "floor"
type = "float"
```

</td>
<td>

```sh
jm function magnitude_db \
  --module dsp \
  --param \
    "x:float _Complex[]" \
  --param "floor:float" \
  --return-type void \
  --out-type \
    float
```

</td>
</tr>

<tr>
<td>

```python
# Path + enum args; raises on failure
def write_header(
    path: str | os.PathLike,
    total: int,
    sample_type: str = "cf32",
    endian: str = "le",
    fs: float = 1e6,
) -> None: ...
```

</td>
<td>

```toml
# just-makeit.toml — declare both enums first
[[enum]]
name   = "stype"
values = ["cf32", "cf64", "ci32"]

[[enum]]
name   = "endian"
values = ["le", "be"]

# modules/io.toml
[[module.io.functions]]
name         = "write_header"
return_type  = "int"
check_return = true

[[module.io.functions.params]]
name = "path"
type = "path"

[[module.io.functions.params]]
name = "total"
type = "size_t"

[[module.io.functions.params]]
name    = "sample_type"
type    = "int"
enum    = "stype"
default = "cf32"

[[module.io.functions.params]]
name    = "endian"
type    = "int"
enum    = "endian"
default = "le"

[[module.io.functions.params]]
name    = "fs"
type    = "double"
default = "1e6"
```

</td>
<td>

```sh
jm function write_header \
  --module io \
  --param path:path \
  --param total:size_t \
  --param \
    sample_type:enum:stype=cf32 \
  --param \
    endian:enum:endian=le \
  --param fs:double=1e6 \
  --return-type int \
  --check-return
```

</td>
</tr>

<tr>
<td>

```python
# Output length from scalar param
def ciccompmf(
    N: int,
    R: int,
    M: int,
) -> NDArray[np.float64]: ...
```

</td>
<td>

```toml
[[module.resample.functions]]
name     = "ciccompmf"
out_type = "float64[M]"
```

</td>
<td>

```sh
jm function ciccompmf \
  --module resample \
  --param N:uint32_t \
  --param R:uint32_t \
  --param M:uint32_t \
  --out-type \
    'float64[M]'
```

</td>
</tr>

</tbody>
</table>

______________________________________________________________________

## Advanced

| Feature                  | What it does                                                                                                                                                                                                                                                            | CLI                                                                                                    |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| Lift C body              | Inject an existing function body into the generated `<<IMPLEMENT>>` stub                                                                                                                                                                                                | `--impl path/to/file.c::funcname`                                                                      |
| Lift line range          | Inject lines N..M (inclusive, 1-based) instead of a named function body                                                                                                                                                                                                 | `--impl path/to/file.c::12:48`                                                                         |
| Rename on lift           | String substitution applied to the extracted body                                                                                                                                                                                                                       | `--replace old::new`                                                                                   |
| Custom create()          | Override generated field assignments in `<pkg>_<comp>_create()` — add `create_impl = """…"""` to the object section **before any `[[comp.state]]` entries** (uses `obj->` for the local pointer)                                                                        | `create_impl` / `create_impl_file` key; or at scaffold `jm object <comp> --impl create::file.c::fn`    |
| Custom reset()           | Override generated field assignments in `<pkg>_<comp>_reset()` — add `reset_impl = """…"""` to the object section **before any `[[comp.state]]` entries** (uses `state->` for the pointer parameter)                                                                    | `reset_impl` / `reset_impl_file` key; or at scaffold `jm object <comp> --impl reset::file.c::fn`       |
| Custom destroy()         | Splice teardown into `<pkg>_<comp>_destroy()` before the trailing `free(state)` — add `destroy_impl = """…"""` to the object section **before any `[[comp.state]]` entries** (uses `state->`)                                                                           | `destroy_impl` / `destroy_impl_file` key; or at scaffold `jm object <comp> --impl destroy::file.c::fn` |
| Opaque state field       | Declare a pointer/handle struct field with no auto-getter/setter, no kwarg — set `opaque = true` on a `[[comp.state]]` entry. Requires `create_impl` to initialize it (validator enforces).                                                                             | TOML only                                                                                              |
| Ship a C executable      | Scaffold `main()` + CMake target wired to your component                                                                                                                                                                                                                | `jm app --target c`                                                                                    |
| Ship a console script    | Scaffold argparse CLI in `src/<pkg>/cli.py`; register in `[project.scripts]`                                                                                                                                                                                            | `jm app --target console`                                                                              |
| Ship a PEP 723 script    | Scaffold a single `.py` file runnable with `uv run` — no install needed                                                                                                                                                                                                 | `jm app --target pep723`                                                                               |
| Perf annotations         | Add `JM_HOT` / `JM_FORCEINLINE` to every `step()`                                                                                                                                                                                                                       | `just-makeit perf`                                                                                     |
| Reconstruct CLI          | Print the full command sequence that reproduces the project                                                                                                                                                                                                             | `just-makeit script`                                                                                   |
| Split TOML               | Move each object section into `objects/<name>.toml` (objects only; modules stay inline)                                                                                                                                                                                 | `just-makeit split-objects`                                                                            |
| Migrate to fragments     | Move every object **and** module into `objects/<name>.toml` / `modules/<name>.toml`, leaving the manifest with `[project]` + include globs                                                                                                                              | `just-makeit migrate-to-fragments`                                                                     |
| New with inline manifest | Start a fresh project on the legacy single-manifest layout (fragments are the default)                                                                                                                                                                                  | `jm new <proj> --no-fragments`                                                                         |
| Generate CI              | Emit a GitHub Actions / Woodpecker workflow that runs `make && make test`                                                                                                                                                                                               | `jm ci [--provider woodpecker]`                                                                        |
| Apply manifest           | Regenerate glue (`_ext.c`, `.pyi`, `CMakeLists.txt`) and refresh `_core.h` declarations from the TOML; `_core.c` and the inline `step()` body stay yours, untouched                                                                                                     | `just-makeit apply`                                                                                    |
| Regenerate component     | Delete every file a component owns and rebuild from the manifest — by default lifts hand-written `_core.c`/`_core.h` bodies out first and splices them back in by function name (`--discard` for a clean reset). Manifest is left intact (`git stash` first regardless) | `just-makeit regenerate <comp>`                                                                        |
| Dry run                  | Show what would be compiled without building                                                                                                                                                                                                                            | `just-makeit dry-run`                                                                                  |
| Extra link libs          | Link a module against an additional library not owned by jm — add `extra_link_libs = ["mylib", "m"]` under `[module.X]`                                                                                                                                                 | `jm module X --extra-link-libs LIB` (at creation)                                                      |
| Extra types              | Register a hand-written CPython type from a `*_extra.c` file in `PyInit_` — add `extra_types = ["MyType"]` under `[module.X]`                                                                                                                                           | `jm module X --extra-types NAME` (at creation)                                                         |
| Hand-written method      | Register a CPython function you wrote in the object's `_extra.c` — add `[[<obj>.extra_methods]]` rows (`name`, `fn`, `flags`, `args`, `returns`, `doc`); jm writes its method-table row, a prototype and the `.pyi` member (gh-1997)                                    | (manifest only)                                                                                        |
| Streaming iterator       | `stream(block, *, count, on_block)` + `__iter__` (`streamable = "true"`)                                                                                                                                                                                                | `jm object <comp> --arg-type void --streamable`                                                        |
| Serializable state       | `state_bytes()` / `get_state()` / `set_state()` (`serializable = "true"`)                                                                                                                                                                                               | `jm object <comp> --serializable`                                                                      |
| Second class, one core   | A view: a second Python class over the same C core, with its own constructor                                                                                                                                                                                            | `jm view <obj> <Class> --module M --create-fn fn`                                                      |
| Zero-copy view           | A method returns a numpy view of memory the C state owns (`borrow = true`)                                                                                                                                                                                              | `jm method <comp> <name> --borrow`                                                                     |

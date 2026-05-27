---
hide:
  - toc
---

# Quick reference — Python → TOML → CLI

Each row shows an annotated Python stub, the `just-makeit.toml` fragment that
produces it, and the CLI command that writes that fragment.  *TOML only* means
the feature is not reachable from the CLI and must be written by hand (or via
`jm apply <fragment>`).

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
# Buffer step
def step(
    self,
    x: NDArray[np.complex64],
) -> NDArray[np.complex64]: ...
```

</td>
<td>

```toml
[comp]
arg_type = "float _Complex[]"
```

</td>
<td>

```sh
jm object comp \
  --arg-type \
    "float _Complex[]"
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

## Constructor parameters (`--no-state` objects)

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
    coeff: NDArray[np.complex64],
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
# Optional 2-D array
def __init__(
    self,
    bank: NDArray[np.float32]
         | None = None,
    rate: float = ...,
) -> None: ...
# bank → fir_create_poly(d0,d1,ptr,rate)
# None → fir_create(rate)
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

*TOML only* — add to
`just-makeit.toml`, then
run `jm apply`.

</td>
</tr>

<tr>
<td>

```python
# Dtype-dispatched array
# int16 → real_create_fn
# other → create_fn
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
    x: NDArray[np.complex64],
) -> NDArray[np.complex64]: ...
```

</td>
<td>

```toml
[[comp.methods]]
name            = "execute"
variable_output = true
```

</td>
<td>

```sh
jm method comp execute \
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
    x: NDArray[np.uint32],
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
return_type     = "uint32_t[]"
variable_output = true
multi_output    = ["uint8_t[]"]
```

</td>
<td>

```sh
jm method comp execute \
  --return-type \
    "uint32_t[]" \
  --variable-output \
  --multi-output \
    "uint8_t[]"
```

</td>
</tr>

<tr>
<td>

```python
# Struct-list return
def find_peaks(
    self,
    x: NDArray[np.float32],
) -> list[tuple[int, float]]: ...
```

</td>
<td>

```toml
[[comp.methods]]
name        = "find_peaks"
arg_type    = "float[]"
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

*TOML only* for field types.
Scaffold with `jm method`,
then edit `just-makeit.toml`
and run `jm apply`.

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
# Module-level function
def apply(
    x: NDArray[np.float32],
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
    x: NDArray[np.complex64],
    floor: float,
) -> NDArray[np.float32]: ...
```

</td>
<td>

```toml
[[module.dsp.functions]]
name     = "magnitude_db"
out_type = "float"
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

*TOML only* — set `out_type`
to `"dtype[param]"` after
scaffolding, then run
`jm apply`.

</td>
</tr>

</tbody>
</table>

______________________________________________________________________

## Advanced

| Feature | What it does | CLI |
|---------|-------------|-----|
| Lift C body | Inject an existing function body into the generated `<<IMPLEMENT>>` stub | `--impl path/to/file.c::funcname` |
| Rename on lift | String substitution applied to the extracted body | `--replace old::new` |
| Custom create() | Override generated field assignments in `<comp>_create()` — add `create_impl = """…"""` to the object section **before any `[[comp.state]]` entries** (uses `obj->` for the local pointer) | TOML only |
| Custom reset() | Override generated field assignments in `<comp>_reset()` — add `reset_impl = """…"""` to the object section **before any `[[comp.state]]` entries** (uses `state->` for the pointer parameter) | TOML only |
| Perf annotations | Add `JM_HOT` / `JM_FORCEINLINE` to every `step()` | `just-makeit perf` |
| Reconstruct CLI | Print the full command sequence that reproduces the project | `just-makeit script` |
| Split TOML | Move each object section into `objects/<name>.toml` | `just-makeit split-objects` |
| Regenerate files | Re-emit all files implied by the current TOML (add-only) | `just-makeit apply` |
| Dry run | Show what would be compiled without building | `just-makeit dry-run` |
| Extra link libs | Link a module against an additional library not owned by jm — add `extra_link_libs = ["mylib", "m"]` under `[module.X]` | TOML only |
| Extra types | Register a hand-written CPython type from a `*_extra.c` file in `PyInit_` — add `extra_types = ["MyType"]` under `[module.X]` | TOML only |

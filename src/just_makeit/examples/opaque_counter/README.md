# opaque_counter example

The simplest possible opaque-state object: a heap-allocated counter that Python
can increment and query, demonstrating the `create_impl` / `destroy_impl`
lifecycle without any scalar state fields.

## TL;DR — see it work first

```sh
just-makeit example opaque_counter
# opaque_counter: PASSED
```

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Or with `pip` if just-makeit is already installed:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```

______________________________________________________________________

## What it demonstrates

- Declaring an **opaque state field** (`uint64_t *` counter on the heap)
- Writing `create_impl` / `destroy_impl` bodies inline in the TOML fragment
- Why opaque fields have **no auto-generated constructor param, getter, or
    setter** — Python sees nothing of the pointer
- The `no_state = "true"` flag combined with opaque fields (the auto-generated
    struct machinery is bypassed; you own the struct entirely)
- The `jm apply <fragment>` workflow: declare the component once in TOML,
    materialize everything with one command

______________________________________________________________________

## 1. Write the fragment

```toml
# counter.toml
[counter]
arg_type     = "void"
return_type  = "uint64_t"
mutable      = "true"
no_state     = "true"
create_impl  = """
obj->count = calloc(1, sizeof(uint64_t));
if (!obj->count) { free(obj); return NULL; }
"""
destroy_impl = "free(state->count);"

[[counter.state]]
name   = "count"
type   = "uint64_t *"
opaque = true
```

Two things to notice:

- `no_state = "true"` suppresses the auto-generated constructor parameters and
    getter/setter scaffolding for all fields. Combined with the `opaque = true`
    field, Python gets a plain `Counter()` with no arguments.
- The `create_impl` body sees `obj` (the freshly `calloc`'d struct pointer);
    `destroy_impl` sees `state`. The trailing `free(state)` is appended
    automatically.

______________________________________________________________________

## 2. Apply and build

```sh
just-makeit new opaque_demo
cd opaque_demo
just-makeit apply ../counter.toml
cmake -B build && cmake --build build
ctest --test-dir build
```

`jm apply` copies `counter.toml` into `objects/counter.toml`, registers it
via `include = ["objects/*.toml"]`, and materialises every file the spec
implies — C source, binding, tests, type stub, CMake wiring — all green on
first build.

______________________________________________________________________

## 3. Implement step()

Open `native/inc/counter/counter_core.h` and fill in the stub:

```c
static inline uint64_t
counter_step(counter_state_t *state)
{
    return ++(*state->count);
}
```

______________________________________________________________________

## 4. Use from Python

```sh
pip install -e .
```

```python
from opaque_demo import Counter

c = Counter()
print(c.step())   # 1
print(c.step())   # 2
c.reset()
print(c.step())   # 1

with Counter() as c2:
    c2.step()
    c2.step()
    print(c2.step())  # 3
# destroy() frees the heap counter automatically
```

______________________________________________________________________

## Key concepts

**Opaque fields are invisible to Python.** The `uint64_t *count` field appears
in the C struct but generates no `__init__` parameter, no getter, and no setter.
`create_impl` initialises it; `destroy_impl` releases it; `reset_impl` (if
provided) preserves or re-zeros it.

**`create_impl` sees `obj`, not `state`.** The fresh struct is named `obj`
inside `create_impl` to avoid a C redeclaration error when a state field is
called `state`. Every other lifecycle function uses `state`.

**Always pair `create_impl` with `destroy_impl` for owned pointers.** The
validator requires `create_impl` when opaque fields exist; it does not require
`destroy_impl` (because some pointers are borrowed). For owned heap memory,
always add teardown.

## See also

- [Declarative scaffolding — opaque state fields](../declarative-scaffolding.md#opaque-state-fields-pointers-and-handles)
- [delay_line example](delay_line.md) — a realistic circular buffer using the same pattern
- [Types reference — state variable types](../types.md#state-variable-types)

# Shared process state

A component compiled into more than one extension module is linked
**statically into each one**, and CPython imports extensions `RTLD_LOCAL`.
So each `.so` gets its own copy of every file-scope `static` in that
component's core.

For a pure kernel that is correct, and it is what the OBJECT-library wiring
exists for. For a primitive whose whole contract is *one per process* — a
signal flag, a registry, a global clock — it is silently wrong.

## The symptom

Nothing fails. That is the difficulty.

```python
it = Interrupt(...)          # doppler.interrupt
buf = F32Buffer(1024)        # doppler.buffer
threading.Thread(target=lambda: buf.wait(64)).start()
it.interrupt()               # sets doppler.interrupt's copy of the flag
# -> the ring wait spins forever; it is reading a different variable
```

Every test can pass while this is broken, and in doppler every test did — the
only setter and the only wait anybody exercised happened to live in the same
`.so`.

## 1. Find out whether you have it

```console
$ jm status --shared-cores
SHARED CORES (3) — component core(s) statically linked into more than one extension module:
  ◆ dp_interrupt_core — interrupt, stream, buffer
  ◆ fft_core — spectral, analyzer
  ◆ acc_trace_core — accumulator, measure
```

This is **reported, never counted** — it does not affect the exit code, and
`jm status` does not print it unless you ask. Most entries are correct: a
kernel shared between modules is the normal case. jm reports the linkage it
owns and does not read your C to guess which of these holds process-global
state; only you know that.

The same list is always present in `jm status --json` under `shared_cores`,
where a script can filter it.

## 2. Declare the one that matters

```toml
[dp_interrupt]
process_global = true
```

## 3. Implement two accessors

jm cannot do this part. The state is yours — declared in your `_core.c` and
reached by your own code on every access — so nothing generated can allocate
it or route reads through a pointer it does not own.

`jm apply` writes `native/inc/<comp>/<comp>_procglobal.h` declaring exactly
what to implement. Hold the state behind one pointer:

```c
static dp_interrupt_state_t  g_own;
static dp_interrupt_state_t *g_cur = &g_own;

void *dp_interrupt_state_ptr(void)  { return (void *)g_cur; }
void  dp_interrupt_state_adopt(void *shared)
{ if (shared) g_cur = (dp_interrupt_state_t *)shared; }
```

and read through `g_cur` everywhere else. Adoption happens at import, before
any of your code runs, so nothing here has to be thread-safe.

## 4. `jm apply`

That is the whole workflow. jm generates the rendezvous into every linking
module's `PyInit_`:

- the **owner** — the module that holds the component — publishes a
    project-qualified `PyCapsule` over its state;
- every **other** linking module imports the owner and adopts the pointer.

Import order does not matter. A module that adopts pulls its owner in itself,
so a user who never names the owning module still gets one shared state.

## What jm refuses, and what it only warns about

They are different situations, and jm treats them differently.

**Refused: the owning module is `no_generate`.** jm writes no `PyInit_`
there, so nothing publishes the state and no adopter anywhere in the project
can work. There is no edit to another module that helps.

**Warned: an *adopting* module is `no_generate`.** Every other module still
shares one state; that one keeps its own copy until you add the adopt
yourself:

```
warning: module 'legacy' links flag_core and is `no_generate`, so jm writes
no PyInit_ there: it keeps its OWN copy of flag's process-global state while
every other module shares one. Add the adopt to its hand-written binding —
native/inc/flag/flag_procglobal.h shows it, with the owner, attribute and
capsule names as #defines.
```

That is a real instruction, not a gesture. The generated header carries the
three names you need, so a hand-written binding joins the rendezvous with no
guessing:

```c
#include "flag_procglobal.h"

PyObject *own = PyImport_ImportModule(FLAG_PG_OWNER);
PyObject *cap = PyObject_GetAttrString(own, FLAG_PG_ATTR);
flag_state_adopt(PyCapsule_GetPointer(cap, FLAG_PG_CAPSULE));
```

(error handling omitted — every pointer there can be `NULL`).

`FLAG_PG_OWNER` names the **extension module**, not the package —
jm's layout is `<pkg>/<mod>/<mod>.so` behind a re-exporting
`__init__.py`, and the capsule is published on the `.so`'s own module
object (gh-1134).

## What this does not cover

- **Non-CPython consumers.** A C binary linking one archive has one copy and
    is unaffected. This is specific to several `.so` files in one process.
- **Anything but the pointer swap.** Adoption happens once, at import. If your
    state needs locking between threads afterwards, that is still yours.

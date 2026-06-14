# Arguments: positional vs keyword

just-makeit generates each binding's argument parsing to balance **call
ergonomics** against **per-call speed**. This page explains what jm emits where,
and the measured cost behind those choices — so you know when a call is cheap
and when it isn't.

## The rule

> **Positional-only** for the per-sample hot path (`step()`, `steps()`);
> **positional-or-keyword** everywhere a human writes the call by name —
> constructors, named methods, and module-level functions.

| Generated binding                | Parsing                             | Callable by keyword?   | Why                                                                                                               |
| -------------------------------- | ----------------------------------- | ---------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `step(x)` / `steps(n)`           | `PyArg_ParseTuple` (`METH_VARARGS`) | No (positional)        | Per-sample / per-block hot loop; single arg, so keywords add no clarity and the loop runs millions of times       |
| Constructor (`__init__`)         | `PyArg_ParseTupleAndKeywords`       | **Yes**                | Called once per object — cost is irrelevant, and multi-`init_param` ctors read far better by name                 |
| Named methods (`jm method`)      | `PyArg_ParseTupleAndKeywords`       | **Yes**                | Usually not the innermost loop; keyword clarity wins                                                              |
| Module functions (`jm function`) | `PyArg_ParseTupleAndKeywords`       | **Yes** (since 0.19.5) | Often multi-param (and `out=`-param); named args are clearer and the cost is ~free when callers pass positionally |

So a generated function can be called either way:

```python
from my_pkg import dsp

dsp.scale_add(x, out, 2.0, 1.0)                  # positional — fastest
dsp.scale_add(x=x, out=out, gain=2.0, bias=1.0)  # keyword — self-documenting
dsp.scale_add(x, out, gain=2.0, bias=1.0)         # mixed — fine
```

while the hot path stays positional:

```python
y = filt.step(x)          # positional only
ys = filt.steps(block)    # positional only
```

## The cost behind it

Micro-benchmark of the two CPython parsers on the *same* signature
(`PyArg_ParseTuple` vs `PyArg_ParseTupleAndKeywords`), best-of-7 × 5M calls,
CPython 3.12 and 3.14. A bare `METH_NOARGS` call is the ~16 ns floor; a 1-arg
positional parse is ~25 ns.

| Scenario                                       | Cost                               |
| ---------------------------------------------- | ---------------------------------- |
| **Keyword-*capable*, but called positionally** | **~0–5 ns/call** (within noise)    |
| **Called with keywords**                       | **~12–25 ns per keyword argument** |

Two very different costs, and conflating them is the trap:

- **Capability is nearly free.** Making a binding accept keywords adds almost
    nothing *as long as callers still pass positionally* — you only pay if you use
    it. This is why functions/methods/ctors are all keyword-capable.
- **Passing keywords scales with arg count.** Each keyword argument is matched
    by name against the parameter list (~12–25 ns each). A 3-arg call is ~2× a
    positional one. That's a deliberate ergonomics-for-speed trade the *caller*
    makes at each call site.

### Where the cost lands: per-sample vs per-block

The same nanoseconds matter very differently depending on how often the call
fires — which is why `step()` and `steps()` are treated differently:

- **`step()` is per-sample.** Its parse cost is paid on *every* sample, so it
    stays **positional-only**. A *keyword* call on a per-sample binding is
    catastrophic: `step(x, gain=…)` measures **+47 ns — ~3.4× the entire call** —
    multiplied by every sample in the stream.
- **`steps()` is per-block.** One parse amortises over the whole block (that same
    +47 ns keyword call spread across a 1024-sample block is ~0.05 ns/sample), so
    `steps()` can afford keyword arguments. Its `out=` variant
    (`steps(x, out=buf)`) is keyword-capable for exactly this reason.

Independently of arguments: prefer **`steps(block)`** over a per-sample `step()`
loop — one call amortises the parse (and the Python call overhead) over the
whole block.

### Default / optional arguments

Declaring an argument optional (a default value, `"d|dd"` in the format string)
is **free to declare and cheaper to omit**:

| Call (1 required + 2 optional)                                | Cost   |
| ------------------------------------------------------------- | ------ |
| same 1-arg signature with *no* optionals, called positionally | ~23 ns |
| optional-tail, both defaults **omitted** (the common case)    | ~24 ns |
| optional-tail, all 3 passed                                   | ~35 ns |

CPython doesn't parse or convert an argument you didn't pass, so a call that
leans on defaults is *faster*, not slower. The cost tracks **how many arguments
you actually pass** (and whether by keyword), never whether they *could* have
been passed.

**The common-default path does not subsidise the override path.** If 90% of
calls take the defaults and 10% override, the 90% pay only ~1 ns total
(≈0.5 ns/arg, noise) for those optional parameters merely *existing* in the
signature — essentially the same as if they weren't there. The 10% who override
pay ~+5 ns *per extra value they pass* — i.e. they pay for what they use. So
adding optional/defaulted parameters optimises the common call for free and
charges only the callers who opt into customisation.

**The rule of thumb: a parameter that carries a default should be optional.**
just-makeit already applies this to **constructors** — a scalar `init_param`
with a `default` is emitted *after* the `|` in the parse format, so you can omit
it and get the default:

```python
Engine(gain=2.0)   # `rate` and any other defaulted init_params omitted → defaults
```

**Optional defaults do *not* require keyword capability.** `PyArg_ParseTuple`
takes optional *positional* arguments via `|` (`"O|d"`) while staying
`METH_VARARGS` — no keyword machinery at all. Measured against today's
positional `step(x)`:

| call                                            | Δ vs `step(x)`      |
| ----------------------------------------------- | ------------------- |
| `step(x)` — optional arg omitted (the hot path) | **+0.7 ns** (noise) |
| `step(x, gain)` — override passed positionally  | +4 ns               |
| if kw-capable: `step(x, gain=…)` — keyword call | **+47 ns**          |

So a defaulted parameter lowers differently by call site — the cost model picks
the form:

1. **functions / named methods** → keyword-capable
    (`PyArg_ParseTupleAndKeywords`, `|`). Multi-param, not the inner loop;
    keyword clarity is worth it and is free unless used.
1. **`step()`** (per-sample) → **positional-optional only** (`PyArg_ParseTuple`
    with `|`); never `METH_KEYWORDS`. The omit path is free (+0.7 ns); a keyword
    call would cost ~3× the call, *per sample*.
1. **`steps()`** (per-block) → keyword-capable is fine (the parse amortises over
    the block); positional-optional is fine too.

This is the "adjust in the hot path" story made concrete: `step(x)` takes the
default, `step(x, override)` passes the override positionally — both effectively
free, no keyword tax.

**Module functions and named methods** support this today —
`jm function fn --param gain:double=1.0` and
`jm method obj m --param gain:double=1.0` both make `gain` an optional
keyword/positional arg: `fn(x)` uses `1.0`, while `fn(x, 2.0)` or
`fn(x, gain=2.0)` overrides. (Adding a defaulted param also makes named methods
keyword-capable, the same as functions.) Defaulted params must come after
required ones, and (for now) only plain scalars take defaults.

**`steps()` overrides** are state-backed rather than compile-time constants. Flag
a state field `controllable = true` in the manifest and it becomes an optional,
keyword-capable `steps()` argument that defaults to the *live* field value:

```toml
[[amp.state]]
name = "gain"
type = "float"
default = "2.0"
controllable = true   # -> steps(x, gain=...) overrides self->gain for the block
```

`amp.steps(x)` reads the current `gain`; `amp.steps(x, gain=10.0)` (or
positionally, `amp.steps(x, None, 10.0)`) overrides it for that call only — the
override never mutates the field, so `get_gain()` is unchanged afterwards. The
override threads into the C `amp_steps(state, in, n, out, gain)` signature (the
one declared, intentional change to the sacred core), and the binding sources it
`arg-if-provided else self->gain`. PR-1 supports the blockwise array-in /
array-out shape with real-scalar (float/int) fields; other shapes and complex
scalars are rejected at generation with a clear error.

## Going faster than both

Both parsers build an args tuple first. The real ceiling-breaker is
`METH_FASTCALL` / the vectorcall protocol, which skips the tuple entirely (~16 ns
floor). just-makeit does not emit it today; the positional `step()`/`steps()`
path is the fastest binding it currently generates. If you have a hand-written
inner loop that dominates, prefer batching through `steps()` (one call amortises
the parse over a whole block) over calling `step()` per sample.

## TL;DR

- Call generated **functions/methods/constructors** however reads best — keyword
    args cost you only when *you* use them, ~12–25 ns each.
- Keep tight loops on `step()`/`steps()` and pass **positionally**.
- Prefer **`steps(block)`** over per-sample `step()` — it amortises the
    argument-parse cost across the whole block.

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

This is also why `step()`/`steps()` stay positional-only: they're single-arg and
run in tight loops, so even the ~3–5 ns capability tax isn't worth paying, and
keywords would add no readability.

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

Module-function and method parameters are all required today (their CLI form,
`--param name:type`, carries no default), so there is nothing to make optional
yet. If a parameter ever gains a default, it should follow the constructor's
lead and become optional — the measurements above show that costs nothing for
the callers who rely on the default.

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

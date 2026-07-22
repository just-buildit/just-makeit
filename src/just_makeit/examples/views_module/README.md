# Views — two Python classes over one C core

This example demonstrates [`jm view`](../commands/extend.md#just-makeit-view):
a **second Python class over the same generated C core**. The two classes share
one `acc_state_t`, one `acc_core.c`, and one `step()`; they differ only in the C
constructor they call, the constructor arguments they take, and the Python
surface they expose.

Reach for a view when one algorithm has two front doors — a continuous mode and
a burst mode, an empty accumulator and a pre-seeded one — and duplicating the
object would duplicate the C.

Run it end to end:

```sh
jm example views_module
```

______________________________________________________________________

## The object, then the view

The parent is an ordinary module object: an accumulator with one state variable
`sum`, whose `step(x)` adds `x` and returns the running total. It gets a `total()`
method and a field-backed `depth` property:

```sh
jm new acc_bank
jm module bank
jm object acc --module bank \
    --state sum:double:0.0 \
    --arg-type double --return-type double --mutable
jm method acc total --module bank --arg-type void --return-type double
jm property acc depth --module bank --type size_t --field --doc "parent depth"
```

The view is one command. It names the new class, the C constructor it calls,
its own constructor parameters, and what it trims:

```sh
jm view acc SeededAcc --module bank \
    --create-fn acc_create_seeded \
    --init-param seed:double:0.0 \
    --exclude-method total
```

That does four things:

- records a `[[acc.views]]` entry in the manifest;
- injects `acc_state_t *acc_create_seeded(double seed);` into `acc_core.h`;
- appends an `<<IMPLEMENT>>` stub for it to the sacred `acc_core.c`, so the
    module still compiles before you have written a line;
- regenerates the module glue with a second class registered on it.

`--create-fn` is required and must differ from the parent's `acc_create` — a
view exists precisely to build from a different constructor.

______________________________________________________________________

## A view diverges, it does not only trim

`--exclude-property` and `--exclude-method` remove parent members from the
view. To go the other way — **add** a member the parent lacks, or **override**
one it has — pass `--view <ClassName>` to `jm property`, `jm method`, or
`jm warning`:

```sh
# adds `runs` to SeededAcc only (a field on the shared acc_state_t)
jm property acc runs --module bank --type size_t --field \
    --doc "reseed count" --view SeededAcc

# same name as the parent's property -> overrides its docstring on the view
jm property acc depth --module bank --type size_t --field \
    --doc "seed depth" --view SeededAcc
```

The resulting manifest is the whole story — one object, one nested view:

```toml
[[acc.views]]
class_name = "SeededAcc"
create_fn = "acc_create_seeded"
exclude_methods = ["total"]

[[acc.views.init_params]]
name = "seed"
type = "double"
default = "0.0"

[[acc.views.properties]]
name = "runs"
type = "size_t"
doc = "reseed count"
field = true

[[acc.views.properties]]
name = "depth"
type = "size_t"
doc = "seed depth"
field = true
```

______________________________________________________________________

## The C you write

Two bodies, both in the shared core. `step()`:

```c
state->sum += x;
return state->sum;
```

and the view's alternate constructor, which reuses the parent's:

```c
acc_state_t *
acc_create_seeded (double seed)
{
  acc_state_t *s = acc_create (seed);
  return s;
}
```

There is no second `step()`, no second struct, and no second core library —
the generated `native/src/bank/CMakeLists.txt` contains `acc_core` and nothing
named `seededacc_core`. The view is pure generated glue: it lands in its own
binding fragment `bank_ext_seededacc.c` alongside the parent's
`bank_ext_acc.c`, and both are registered by the one aggregating `bank_ext.c`.

______________________________________________________________________

## The two classes

After a build, one `.so` exports both — same `step()` behaviour, different
starting points, different surfaces:

```python
from acc_bank.bank import Acc, SeededAcc

a = Acc(sum=0.0)
assert a.step(1.0) == 1.0
assert a.step(2.5) == 3.5

s = SeededAcc(seed=10.0)          # its own constructor shape
assert s.step(1.0) == 11.0
assert s.step(2.5) == 13.5

# trimmed: total() is on the parent, excluded from the view
assert a.total() == 3.5
assert not hasattr(SeededAcc, "total")

# added: `runs` exists only on the view
assert hasattr(SeededAcc, "runs")
assert not hasattr(Acc, "runs")

# overridden: both have `depth`, with different docs
assert "parent depth" in Acc.depth.__doc__
assert "seed depth" in SeededAcc.depth.__doc__
```

Excluding a method drops only its Python wrapper and its `PyMethodDef` entry —
`acc_total()` is still in the C core, so there is no dangling symbol and the
parent keeps working. The generated `bank.pyi` carries both classes, with
`SeededAcc.__init__` typed to its own `seed: float` parameter.

Views are a **module-object feature**: the multi-type module machinery is what
registers the extra class, so `jm view` requires `--module`.

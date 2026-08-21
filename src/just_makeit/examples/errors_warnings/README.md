# errors_warnings example

The four ways a component can tell Python that something is wrong — and they
are four, not one, because C has more failure modes than it has channels to
report them on.

| what happened | how C says it | how Python hears it | declared by |
| --- | --- | --- | --- |
| `create()` refuses | returns `NULL` | an exception, at construction | `just-makeit error` |
| `create()` succeeded, with a caveat | a `bool` field on the state struct | a `warning`, after construction | `just-makeit warning` |
| a call fails | an `int` status, non-zero is bad | an exception; the method returns `None` | `--status-return --error` |
| a call fails, but usually returns a value | an `int` that is a value unless negative | an exception, or the `int` | `--error-negative --error` |

The first two are `create()`'s problem and the last two are a method's. All
four are pure glue — **no sacred file is touched by declaring them**, which is
the point: they are a translation layer over signals your C already emits.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example errors_warnings
# errors_warnings: PASSED
```

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Pass a custom path to keep the venv somewhere persistent:

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh) -- ~/my-venv
```

Or with `pip` if just-makeit is already installed:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```

---

## 1. Scaffold an allocator

```sh
just-makeit new budget
cd budget

just-makeit object allocator \
    --init-param "capacity:size_t" \
    --init-param "slots:size_t" \
    --state "n_slots:size_t:0" \
    --state "remaining:size_t:0" \
    --state "degraded:bool:false" \
    --arg-type size_t \
    --return-type size_t
```

`capacity` and `slots` are `--init-param`s: they are what the caller passes,
and `create()` consumes them without storing them. The three `--state` fields
are what the object *derives* and keeps — declaring init-params is what keeps
them out of the constructor signature, so `Allocator(capacity=9, slots=3)` is
the whole public API.

`degraded` is the one to watch. It is an ordinary `bool` on the state struct
that `create()` sets, and in step 3 it becomes the entire mechanism behind a
Python warning.

---

## 2. Declare the four channels

```sh
# ── channel 1: create() refuses ─────────────────────────────────────────────
just-makeit error allocator \
    --category ValueError \
    --message "capacity must cover at least one unit per slot"

# ── channel 2: create() succeeded, with a caveat ────────────────────────────
just-makeit warning allocator \
    --condition degraded \
    --category RuntimeWarning \
    --message "capacity is not divisible by slots; the remainder is unusable"

# ── channel 3: an int that carries nothing but status ───────────────────────
just-makeit method allocator take \
    --arg-type size_t \
    --return-type int \
    --status-return \
    --error ValueError \
    --error-message "requested more than remains"

# ── channel 4: an int that is a value unless it is negative ─────────────────
just-makeit method allocator peek \
    --arg-type size_t \
    --return-type int \
    --error-negative \
    --error IndexError \
    --error-message "no such slot"
```

What each one is really doing:

- **`just-makeit error`** replaces jm's default translation of a `NULL` from
    `create()`. Without it every refusal surfaces as `MemoryError`, which is
    true only for an actual allocation failure and misleading for every other
    reason — and uncatchable the way a caller would naturally reach for it.
    The command prints the limit of the design as it runs: NULL is NULL, so
    *every* failure now reports as `ValueError`, a genuine out-of-memory
    included.
- **`just-makeit warning`** exists because C has no channel for "succeeded,
    but". `create()` returns a pointer or it does not. So the component writes
    a `bool` on its state, and `--condition degraded` tells jm to emit a
    `PyErr_WarnEx` reading that field right after a successful construction.
- **`--status-return`** says the `int` is *only* a status. The method returns
    `None` in Python, and any non-zero raises.
- **`--error-negative`** says the `int` is a **value** unless it is negative.
    `peek()` returns an `int` in Python, and only a negative raises.

Both method flags need `--return-type int`. That is not cosmetic:
`--status-return` with `--return-type size_t` is accepted and generates
`int _rc = allocator_take(...)` against a `size_t` prototype, which compiles
and silently truncates.

---

## 3. Implement — the C only ever sets flags and returns codes

```python
"""Implement create(), take() and peek().

Every anchor below is asserted before it is replaced. A silent `str.replace`
that matches nothing leaves the scaffold's own placeholder in place, the
build still succeeds, and the demo then fails somewhere unrelated -- which is
exactly how this script was wrong the first time it was written.
"""

from pathlib import Path

CORE = Path("native/src/allocator/allocator_core.c")
CTEST = Path("native/tests/test_allocator_core.c")

CREATE_OLD = """\
    allocator_state_t *obj = calloc(1, sizeof(*obj));
    if (!obj)
        return NULL;
    obj->n_slots = 0;
    obj->remaining = 0;
    obj->degraded = false;
    return obj;"""

CREATE_NEW = """\
    allocator_state_t *obj;

    /* Channel 1 -- refuse. NULL is the ONLY failure signal create() has, so
     * every reason to refuse funnels through it and arrives in Python as the
     * single category `just-makeit error` declared.
     */
    if (slots == 0 || capacity < slots)
        return NULL;

    obj = calloc(1, sizeof(*obj));
    if (!obj)
        return NULL;
    obj->n_slots   = slots;
    obj->remaining = (capacity / slots) * slots;
    /* Channel 2 -- succeeded, but not with what was asked for. Construction
     * is complete and valid; this is a "here is the best I could do" flag,
     * which is why it is a warning and not a refusal.
     */
    obj->degraded  = (capacity % slots) != 0;
    return obj;"""

TAKE_OLD = """\
allocator_take(allocator_state_t *state, size_t x)
{
    (void)state; (void)x;
    return (int)0;
}"""

TAKE_NEW = """\
allocator_take(allocator_state_t *state, size_t x)
{
    /* Channel 3 -- status only. 0 is success; the value of a non-zero code
     * is reported in the exception message but carries no result.
     */
    if (x > state->remaining)
        return 1;
    state->remaining -= x;
    return 0;
}"""

PEEK_OLD = """\
allocator_peek(allocator_state_t *state, size_t x)
{
    (void)state; (void)x;
    return (int)0;
}"""

PEEK_NEW = """\
allocator_peek(allocator_state_t *state, size_t x)
{
    /* Channel 4 -- a value, unless it is negative. A valid slot returns a
     * count the caller keeps; an invalid one returns a negative code that
     * never reaches the caller as a number.
     */
    if (x >= state->n_slots)
        return -1;
    return (int)(state->remaining / state->n_slots);
}"""


# The scaffolded C test constructs with `create(0, 0)`, which was valid until
# create() grew a refusal. Moving it is not incidental tidying: the C test is
# the author's file, and a contract change is exactly when it has to move.
# Covering the refusal there too keeps channel 1 tested at the C layer, where
# it is implemented, and not only through the binding.
CTEST_OLD = """\
    allocator_state_t *obj = allocator_create(0, 0);
    REQUIRE(obj != NULL);

    /* n_slots: getter / setter */
    CHECK(allocator_get_n_slots(obj) == 0);"""

CTEST_NEW = """\
    /* create() refuses what it cannot serve -- 2 units over 3 slots. */
    CHECK(allocator_create(2, 3) == NULL);
    CHECK(allocator_create(9, 0) == NULL);

    allocator_state_t *obj = allocator_create(9, 3);
    REQUIRE(obj != NULL);
    /* An exact fit is not degraded; 10 over 3 would be. */
    CHECK(allocator_get_degraded(obj) == false);

    /* n_slots: getter / setter */
    CHECK(allocator_get_n_slots(obj) == 3);"""

CTEST_REMAINING_OLD = """\
    /* remaining: getter / setter */
    CHECK(allocator_get_remaining(obj) == 0);"""

CTEST_REMAINING_NEW = """\
    /* remaining: getter / setter */
    CHECK(allocator_get_remaining(obj) == 9);"""


def _replace(text: str, old: str, new: str, what: str) -> str:
    assert old in text, (
        f"anchor for {what} not found -- did the scaffold change?"
    )
    return text.replace(old, new, 1)


def main() -> None:
    s = CORE.read_text(encoding="utf-8")
    s = _replace(s, CREATE_OLD, CREATE_NEW, "create()")
    s = _replace(s, TAKE_OLD, TAKE_NEW, "take()")
    s = _replace(s, PEEK_OLD, PEEK_NEW, "peek()")
    CORE.write_text(s, encoding="utf-8")

    t = CTEST.read_text(encoding="utf-8")
    t = _replace(t, CTEST_OLD, CTEST_NEW, "C test construction")
    t = _replace(
        t, CTEST_REMAINING_OLD, CTEST_REMAINING_NEW, "C test remaining"
    )
    CTEST.write_text(t, encoding="utf-8")


if __name__ == "__main__":
    main()
```

Nothing in this file mentions Python. `create()` returns `NULL` or sets a
`bool`; the two methods return an `int`. Every exception and warning the next
step shows is jm's glue reading those, which is why none of this needed a
`#include <Python.h>` and why the declarations in step 2 touched no sacred
file.

---

## 4. Build

```sh
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel 4
ctest --test-dir build --output-on-failure
```

---

## 5. All four, from Python

```python
"""Drive all four failure channels from Python."""

import sys
import warnings

sys.path.insert(0, "src")

from budget import Allocator  # noqa: E402

# ── channel 1: create() refuses ─────────────────────────────────────────
# 2 units cannot cover 3 slots, so create() returns NULL.
try:
    Allocator(capacity=2, slots=3)
    raise AssertionError("expected a refusal")
except ValueError as exc:
    print(f"1. refuse     -> ValueError: {exc}")

# ── channel 2: succeeded, with a caveat ─────────────────────────────────
# 10 over 3 slots leaves a remainder, so construction succeeds and warns.
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    lossy = Allocator(capacity=10, slots=3)
    assert len(caught) == 1, caught
    assert caught[0].category is RuntimeWarning
    print(f"2. caveat     -> RuntimeWarning: {caught[0].message}")
# The object is fully usable -- a warning is not a failure.
assert lossy.get_remaining() == 9

# An exact fit warns about nothing.
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    a = Allocator(capacity=9, slots=3)
    assert not caught, caught
    print(f"   (exact fit) -> {len(caught)} warnings")

# ── channel 3: a status-only int ────────────────────────────────────────
# Success is None, not 0: the int carried nothing worth returning.
assert a.take(5) is None
print(f"3. take(5)    -> None   (remaining now {a.get_remaining()})")
try:
    a.take(100)
    raise AssertionError("expected a refusal")
except ValueError as exc:
    print(f"   take(100)  -> ValueError: {exc}")

# ── channel 4: an int that is a value unless negative ───────────────────
# The same call shape returns a number here, because the int means something.
assert a.peek(0) == 1
print(f"4. peek(0)    -> {a.peek(0)}")
try:
    a.peek(99)
    raise AssertionError("expected a refusal")
except IndexError as exc:
    print(f"   peek(99)   -> IndexError: {exc}")

print("errors_warnings demo: PASSED")
```

```
1. refuse     -> ValueError: capacity must cover at least one unit per slot
2. caveat     -> RuntimeWarning: capacity is not divisible by slots; the remainder is unusable
   (exact fit) -> 0 warnings
3. take(5)    -> None   (remaining now 4)
   take(100)  -> ValueError: requested more than remains (rc=1)
4. peek(0)    -> 1
   peek(99)   -> IndexError: no such slot (rc=-1)
errors_warnings demo: PASSED
```

Two details worth keeping:

- **`take()` returns `None` on success, not `0`.** `--status-return` means the
    `int` carried nothing but status, so there is no result to hand back. Its
    neighbour `peek()` returns a real number from an identically-shaped C
    function, and the only reason they differ is the flag.
- **The failing code reaches the message** — `(rc=1)`, `(rc=-1)`. Your
    `--error-message` says what went wrong in prose; jm appends what the kernel
    actually returned, which is the part you need when several codes share one
    category.

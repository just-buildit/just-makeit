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

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
# gh-273's seeding skip: `capacity`/`slots` are REQUIRED with no default, so
# jm emits a zero-seeded call it does not trust and bails out if the ctor
# refuses it — exactly the ctor this example is about. The patch replaces the
# whole block with a construction that IS valid, which is what the walkthrough
# demonstrates.
CTEST_OLD = """\
    allocator_state_t *obj = allocator_create(0, 0);
    if (!obj) {
        /* capacity, slots: required with no default — a validating
           allocator_create() may reject the zero-seeded call
           above. Pass valid arguments to smoke-test further. */
        printf("test_allocator_core SKIPPED (capacity, slots need seeding)\\n");
        return 0;
    }

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

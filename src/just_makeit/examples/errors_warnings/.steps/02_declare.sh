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

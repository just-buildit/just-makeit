- **`Composer.segments` keeps a generated `bytes` member, and copies a
    `complex` stream (gh-2718).** The segment-list copy duplicated a
    `c_ptr` / `c_len` pair only when both were set and otherwise zeroed the
    length, so a member with a NULL pointer and a length (a generated run of
    bits) came back absent and `Composer(c.segments)` composed a different
    source. It now keeps the whole member and duplicates the pointer only
    when it is set. A `complex` stream was not duplicated at all, so the
    copied source and the composer state both freed one buffer; it is
    duplicated now. The c-face CLI freed a relocated `bytes` field's
    enclosing struct (`src.<name>`) instead of its `c_ptr` member; it frees
    the member.

- **A composer's `execute`, `compose` and `stream` say they share one
    cursor** (gh-2296). All three advance the backing C's single read
    position, so `compose()` after `execute(n)` returns only what
    remains, and a drained composer yields nothing. `compose()`'s
    docstring promised "the full sequence". Each member now states the
    shared cursor, on both faces, and says to build a new composer to
    start over. Behaviour is unchanged.

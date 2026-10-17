- **A recorded app run replays to the same values** (gh-2254). A
    `jm app` C executable's `--record` wrote each `float` / `double` flag
    with `%g`, six significant digits, so `--gain 0.123456789` was
    recorded as `0.123457` and a replay ran with a different value; an
    infinite or NaN flag was recorded as `inf` / `nan`, which is not JSON.
    A function app's C face printed a real result the same lossy way. Both
    now read one conversion derived from the type registry: a real is
    written with the digits that read back to its exact bits (9 for
    `float`, 17 for `double`), a non-finite one as `Infinity` /
    `-Infinity` / `NaN` as the Python face's record writes it, and an
    integer through a 64-bit conversion.

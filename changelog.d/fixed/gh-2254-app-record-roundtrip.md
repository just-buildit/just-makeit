- **A recorded app run replays to the same values, and its csv output
    reads back to the same samples** (gh-2254). A `jm app` C executable's
    `--record` wrote each `float` / `double` flag with `%g`, six significant
    digits, so `--gain 0.123456789` was recorded as `0.123457` and a replay
    ran with a different value; an infinite or NaN flag was recorded as
    `inf` / `nan`, which is not JSON. A function app's C face printed a
    real result the same lossy way, and `--file_type csv` wrote a `cf32`
    sample with `%0.9f` on every face (a composer's c-face CLI included),
    so a sample below 5e-10 -- `1e-12`, any subnormal -- was written as
    `0.000000000`. All of them now read one conversion derived from the
    type registry: a real is written with the digits that read back to its
    exact bits (9 for `float`, 17 for `double`), a non-finite record entry
    as `Infinity` / `-Infinity` / `NaN` as the Python face's record writes
    it, and an integer through a 64-bit conversion. The C and Python faces
    still write byte-identical csv.

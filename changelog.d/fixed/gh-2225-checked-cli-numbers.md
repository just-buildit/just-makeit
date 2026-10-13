- **A generated C command line refuses a bad numeric flag instead of
    running with a wrong value** (gh-2225). A composer's c-face CLI read
    each numeric flag with `strtoull` (or `strtod`) and a cast, and a
    `jm app` C executable with `strtol` and a cast. Neither checked the end
    pointer, `errno` or the declared type's range, so `--count abc` ran
    with `0`, an overflowing count with `ULLONG_MAX`, `--gain 300` on an
    `int8_t` with `44` and `--n -1` on a `uint8_t` with `255`, all with
    exit status 0 and no message. Both faces now read every numeric flag
    through one generated parser: the whole argument at its declared type,
    held to the type's `<stdint.h>` / `<limits.h>` range (a `float` read at
    its own precision), or the tool prints
    `<prog>: --gain: '300' is out of range for int8_t [-128, 127]` and
    exits 2. A valid argument reads exactly as before, in the same grammar
    (decimal for an app, `0x` hex and `0` octal too for a composer). The
    parser is emitted only into a file whose flags call it.

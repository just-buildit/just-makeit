- **A scaffold builds clean under `-Wall -Wextra -Werror`** (gh-1745). Three
    things jm generates failed a project building its C with warnings as
    errors. A string-enum lookup (`_enum_index`, `_enum_index_<Type>`) was
    emitted into extensions that never call it, such as a read-only enum
    property or a handle whose enums are only on getters
    (`-Wunused-function`). It is now emitted only when a setter, parameter or
    constructor argument looks the enum up. The benchmark's `volatile` sinks
    were stored to and never read (`-Wunused-but-set-variable`); each is now
    read once after its timing loop. `jm_bench.h`'s `strncpy` copies
    (`-Wstringop-truncation`, gcc) are now one bounded-copy helper. Existing
    projects get the new `jm_bench.h` from `jm status`'s OUTDATED report. A
    new sweep builds representative shapes, the benchmark included, with
    those flags under gcc and clang. It found two more unused helpers under
    clang, filed as gh-1747 and gh-1748.

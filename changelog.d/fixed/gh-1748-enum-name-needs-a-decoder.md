- **An `[[enum]]` with `enumerators` no longer emits an unused reverse
    lookup** (gh-1748). An enum bound to C constants (gh-1450) got a
    `_enum_<name>_name` function beside its tables, which maps a C value
    back to its choice string. Only a getter or a JSON serializer calls it,
    so a face that only looks the enum up, such as a module function
    parameter, a method parameter, a handle constructor argument or a
    composer's C CLI, carried a function nothing called. clang reports that
    as `-Wunused-function`, which failed a `-Werror` build. It is now emitted
    only where a getter or serializer decodes the enum, decided by the same
    check that emits the decode.

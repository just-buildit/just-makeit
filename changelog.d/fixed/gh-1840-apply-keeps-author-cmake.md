- **`jm apply` no longer erases an OBJECT library you add to a generated
    `native/src/<dir>/CMakeLists.txt`, and `jm status` no longer promises
    an apply that would** (gh-1840). The file is regenerated, so the next
    apply dropped the library without a word: gh-1351's warning names a
    dropped command only when jm never writes it, and jm writes
    `add_library`. Whatever wired the library was left naming a target that
    was gone, so cmake refused to configure, and the apply after that
    deleted the wiring too. `status` meanwhile said "`jm apply` writes the
    missing target_sources() line" for that library. Now `apply` refuses
    before writing anything. It names each library and the
    `<dir>_extra.cmake` hook to move it to, which the generated file
    includes and jm never writes. A library the rewrite keeps is not
    refused: one in a preserved `if()` block, or in a file `status_allow`
    names. A module object's own CMakeLists now honours `status_allow` like
    the others; it alone was rewritten anyway. `jm status` reads a library
    declared in the hook, in any command case, so `UNWIRED` names it while
    nothing wires it. A root line that wires it is no longer reported
    `DANGLING` and deleted by `apply`. `UNWIRED` says `apply` wires a core
    only when the replay's apply leaves the core declared and wired.

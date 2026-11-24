- **`<comp>_procglobal.h` declares its two accessors inside
    `extern "C"` (gh-2754).** The contract header of a `process_global`
    component declared `<stem>_state_ptr` and `<stem>_state_adopt` at file
    scope, so a C++ translation unit that included it gave both C++
    linkage and asked the linker for a mangled name the C core never
    defines. They now sit in the same `#ifdef __cplusplus` block every
    other generated header uses, and `jm apply` rewrites an existing
    project's header. A new test compiles every header jm generates and
    has a C++ compiler redeclare each function it declares `extern "C"`,
    so a header that leaves one out fails the build of jm's own suite.

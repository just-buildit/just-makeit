- **The bundled examples run jm the way the test suite does** (gh-2597).
    Four in-process runners had grown, and only the suite's cleared jm's
    manifest cache before a command (gh-2095) and captured output at the
    file descriptor (gh-1374); in the `enum_constants` and `stale_project`
    examples a cmake, ctest or pytest child's output went past the capture.
    `just_makeit._example.run_cli` is now the one runner, the examples call
    it through `run_jm`, the suite's `run_cli` adapts it, and a test refuses
    `main()` called anywhere else.

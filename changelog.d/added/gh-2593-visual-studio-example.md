- **A bundled example walks a Visual Studio user through Windows**
    (gh-2593). `jm example visual_studio` and its page in the docs gallery go
    from `jm new` in PowerShell to a passing test run: create the `.venv`,
    *File > Open > Folder*, pick **Release (clang-cl)**, *Build All*, then
    Test Explorer and the Python suite, each IDE action next to the
    `cmake --preset` / `cmake --build --preset` / `ctest --preset` line it
    maps to. The example's test runs the PowerShell lines the page shows, so
    the Windows clang-cl CI job now drives the IDE's path, and it checks
    every preset name and the toolbar label against the generated
    `CMakePresets.json`. `docs/windows.md` links to it.

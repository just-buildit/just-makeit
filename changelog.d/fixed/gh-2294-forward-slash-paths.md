- **`upgrade`, `build` and `bench` print project paths with forward
    slashes on every platform** (gh-2294). Eight messages printed a path
    relative to the project the way the OS spells it, so on Windows
    `jm upgrade` listed `native\src\nco\nco_core.c`. That disagreed with
    every other path jm prints and with what the manifest records. They
    now print `native/src/nco/nco_core.c` everywhere.

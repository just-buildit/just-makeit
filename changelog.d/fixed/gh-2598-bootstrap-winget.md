- **A generated project's `bootstrap.toml` installs the Windows toolchain**
    (gh-2598). It had no Windows table, so on Windows `make install-deps`
    installed nothing, and `docs/windows.md`'s prerequisites existed only as
    prose. It now carries a `[dev.winget]` table: the Visual Studio Build
    Tools with the *Desktop development with C++* workload and the *C++
    Clang Compiler for Windows* component (`vs_requires`). `install-deps`
    asks `vswhere` for an instance with both and adds whatever is missing.
    `bootstrap.toml` is create-only, so an existing project gets the table
    by copying it from a fresh `jm new`. jm's own `bootstrap.toml` drops
    `[dev.msys2]`, the retired MinGW toolchain, for the same table plus
    make, uv and Git.

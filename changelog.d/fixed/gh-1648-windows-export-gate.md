- **The `c_prefix` symbol gates run on Windows** (gh-1648). "Every symbol
    `lib<pkg>` exports carries the prefix" was checked by `nm` on the Linux
    and macOS legs only: a DLL has no symbol table, so `nm` over it prints
    `no symbols` and exits 0, and the tests were skipped on `win32` and
    absent from the Windows job besides. One reader, `tests/_exports.py`,
    now serves every platform -- on a clang-cl build it reads the DLL's
    export table (`llvm-readobj --coff-exports`) and the static `.lib`'s
    defined externals, ignoring MSVC's literal-pool names -- and both
    symbol gates run in `Examples (windows-latest, clang-cl)`. A new gate
    fails if a test that reads exports is left off that job.

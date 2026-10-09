- **`make build` configures against the interpreter it is given, so a build
    through an isolated interpreter no longer breaks the next one** (gh-1898).
    `just-build`, which the PEP 517 frontend runs in an environment it then
    deletes, configured the shared `build/` with that environment's interpreter.
    The cache kept its paths, so the next `make` failed on a numpy header that
    was gone, because `build` configured only when `CMakeCache.txt` was missing.
    `build` now drops the cache when it names a different interpreter, and the
    cache rule rebuilds it, NumPy check included. Projects scaffolded before this
    keep their old `Makefile`: it is create-only (see #2164).

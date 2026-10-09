- **Switching `[project] build` reaches the Makefile** (gh-1899). The old
    backend's Makefile was create-only, so `build = "cmake"` on a make project
    left the make Makefile in place: `make` kept building without CMake, and on
    Windows it stopped with a message telling the author to do the switch they had
    just made. `apply` now replaces a Makefile that is byte-identical to the other
    backend's render, and leaves an author's edit alone. `status` reports the
    mismatch as BACKEND drift, not OUTDATED, and `status --json` gains a
    `backend` key. The Windows `$(error)` text is unchanged, so an existing make
    project's Makefile does not become OUTDATED on upgrade.

- **`jm status` UNWIRED no longer promises an `apply` that wires nothing**
    (gh-1626). A `no_generate` module's or a c_dep's OBJECT core gets only
    an `add_subdirectory()` from `jm apply`, yet the listing said apply
    writes the missing `target_sources()` line, so `--check` stayed red
    however often it was re-run. The advice is now per core and read from
    the replay `status` already runs: apply wires it, apply renders it but
    cannot place it (a missing anchor, see UNANCHORED), or apply writes no
    line -- then it prints the lines to add to the root `CMakeLists.txt`
    yourself. `--json` carries the same answer as `apply_wires`.

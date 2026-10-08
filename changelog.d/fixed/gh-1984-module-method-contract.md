- **`jm method` on an object in a module writes the element contract, so
    `jm status --check` passes right after the method that completes a
    pair** (gh-1984). Declaring the second face of a pair -- a writer and a
    reader that speak one `[[<obj>.records]]` element -- brings
    `test_<obj>_invariants.py` into being, and on a standalone object
    `jm method` wrote it. On an object in a module it did not, so
    `status --check` reported the file MISSING on the tree `jm method` had
    just written, and only `jm apply` wrote it. The module re-render that
    every module verb goes through now writes each member's contract, as it
    already wrote each member's link-check table; the standalone re-render
    does the same, and a contract a verb creates, rewrites or deletes is
    announced on its own line.

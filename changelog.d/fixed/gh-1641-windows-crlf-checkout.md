- **A Windows checkout of a jm project is no longer drift** (gh-1641). jm
    writes LF everywhere, Git for Windows checks text out CRLF, and jm
    scaffolded no `.gitattributes`, so a fresh Windows clone read every
    regenerated file as `STALE` and every create-only one as `OUTDATED`, and
    `jm status --check` failed its CI over line endings. `jm new` now writes
    a `.gitattributes` (`* text=auto eol=lf`) that keeps a checkout LF, and
    `jm apply` adds it to an existing project -- which `jm status --check`
    lists as `MISSING` until it does. A file that differs from jm's render
    only in CRLF versus LF is now its own uncounted `LINE ENDINGS` row, a real
    change under a CRLF checkout is still `STALE` and diffs as that change
    alone, and `apply` lists its rewrite to LF as `eol` rather than `update`.
    See [Windows](docs/windows.md#line-endings).

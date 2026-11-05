- **`jm status` compares against one read of the project** (gh-2488). It
    copied the tree, replayed the manifest on the copy, then compared each
    file with the LIVE file read again afterwards, so anything written in
    between -- a `.pytest_cache` entry, a `.ruff_cache` blob, an author's
    notes -- was reported STALE, "`jm apply` will rewrite", although apply
    never touches it, and `--check` failed. Now every comparison is against
    the copy as it was read: a file the replay leaves unchanged is
    unchanged, whatever is written meanwhile. A file jm's replay does
    change whose live copy is rewritten while status runs is listed under
    CHANGED WHILE STATUS RAN (`changed_during_run` in `--json`), never as
    STALE, and fails `--check` unless allowed: status could not compare it,
    and re-running on a quiet tree settles it.

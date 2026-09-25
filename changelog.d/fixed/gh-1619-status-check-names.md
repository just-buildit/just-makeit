- **`jm status --check` names what it fails on** (gh-1619). It collapsed
    its report to one summary line, and the MISSING and STALE listings went
    with it, so a CI run exited 1 on `1 stale` without saying which file.
    Both listings now print under `--check`; the advisory ones stay
    collapsed.

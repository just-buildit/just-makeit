- **`jm status` leaves another checkout inside the project out of it**
    (gh-1713). `status` copied the project directory whole and counted every
    file in it as manifest-owned, so a git worktree placed inside a project
    -- Claude Code's agent worktrees under `.claude/worktrees/` -- was part
    of the count (it doubled a fresh project's, 35 to 70), and a file edited
    in that other branch's tree while `status` ran was reported STALE here,
    with `jm apply` as the advice. A directory holding its own `.git` (a
    clone, a linked worktree, a submodule) is now another checkout: never
    copied, compared or counted, with or without git installed. `jm apply`'s
    change report no longer reads one either.

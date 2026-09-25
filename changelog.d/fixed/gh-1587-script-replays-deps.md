- **`jm script` replays a project's `[project]` dependencies** (gh-1587). It
    rebuilt `jm new` from four hand-listed flags, so a project scaffolded
    with `--find-package`, `--pkg-module` or `--c-dep` replayed as a bare
    `jm new`, and its external-deps block, installed config and `.pc`
    silently lost every dependency. Each entry now replays as its flag
    (`--pkg-module "zlib >= 1.2"` quoted), and a table entry
    (`{ name = ..., cflags = ... }`), which has no CLI spelling, is named in
    a `# NOTE:` instead of dropped.

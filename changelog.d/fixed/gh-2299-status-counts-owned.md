- **`jm status` counts only the files jm owns** (gh-2299). "N
    manifest-owned file(s) match" counted every file the replay left
    untouched, so a `.pytest_cache` entry, a `.ruff_cache` blob, an
    author's notes or a file an older jm wrote and this one no longer
    renders (`jb.toml`) each counted as a match, and two trees differing
    only in untracked files reported different numbers. It now counts
    the files the replay renders plus the manifest and its fragments.
    `_config.manifest_files` is the one list of those, shared by
    `upgrade`, the `c_prefix` respell and `status`.

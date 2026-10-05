- **A release commit is a version bump alone under any uv** (gh-1866).
    `make bump-version` left `uv.lock` to uv: it ran `sync_version.py` under
    a plain `uv run`, then `uv lock`, and each re-locked the whole file,
    stamping the lockfile `revision` of the running uv. uv 0.12.22 and later
    write 5 over this repo's 3, so a release cut with a newer uv changed that
    line too. `make ci-changes` then read the release commit as more than a
    bump, and its CI ran the full matrix, with nothing saying so until CI
    did. `sync_version.py` now writes `uv.lock`'s own version line, the way
    it writes `bootstrap.toml`'s, under `uv run --no-sync`, which never
    re-locks; the bump then only asks `uv lock --check`. The lock's diff is
    its version line under every uv measured (0.11.28 to 0.12.23), its
    revision stays whatever it was, and a lock that needs more than the
    version stops `release-branch` with a message rather than riding into
    the release. A version edited by hand keeps the revision through the
    pre-commit hooks the same way.

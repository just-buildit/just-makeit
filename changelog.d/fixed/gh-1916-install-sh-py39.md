- **The curl installer runs on Python 3.9 and 3.10** (gh-1916). jm has
    supported Python 3.9+ since its `requires-python` was lowered, but
    `install.sh` still refused anything older than 3.11
    (`Python 3.10 found, but 3.11+ is required.`), and the README, the docs
    and every example README told 3.9 and 3.10 users to install with pip
    instead. The installer now accepts the same Pythons pip does: its floor
    is written once and every check and message reads it, and a test runs
    it at `requires-python`'s floor and just below so the two cannot drift
    again. The caveats are gone.

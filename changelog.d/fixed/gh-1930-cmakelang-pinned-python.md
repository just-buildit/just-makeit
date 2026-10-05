- **cmake-format and cmake-lint run under Python 3.15** (gh-1930). CPython
    3.15 refuses capturing groups in `re.Scanner`, and cmakelang's last
    release builds its lexer that way, so every cmakelang entry point
    crashed on its first file when it ran under the Python being tested.
    The 3.15 CI leg reported the crash as `cmake-lint found violations` with
    an empty list, because the test helpers printed stdout alone. cmakelang
    now runs under a pinned Python 3.14 whatever Python the suite uses,
    through one Makefile command that `make format`, `make lint` and the
    tests all use; its version is still the one `pyproject.toml` pins. The
    two test helpers are one, which reports exit 2 as a cmake-lint crash
    rather than as findings and always shows stderr, and fails when it has
    no cmake-lint instead of passing quietly.

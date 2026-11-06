- **A `check_return` function's contract test expects `None`** (gh-2510).
    gh-2127's generated test asserted the C `return_type` (`int`). A
    `check_return` binding instead raises on a nonzero status and returns
    `None`, as its `.pyi` declares, so a correct binding failed its own test.
    The test now reads its expected type from the same place the stub
    does, so the two cannot disagree.

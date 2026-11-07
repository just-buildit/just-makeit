- **A skipped functions contract test's reason fits 79 columns** (gh-2513).
    `ruff format` cannot break a string, so a long `@unittest.skip` reason is
    now written the way ruff leaves it: alone inside the parentheses, or as
    implicit concatenation across lines. Before this, doppler's three skip
    reasons (a `const char *` or `path` parameter with no contract argument)
    were E501 at 79.

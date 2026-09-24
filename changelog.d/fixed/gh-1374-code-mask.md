- **The C scanner behind `apply`, `status` and the binding checks is one
    regex pass** (gh-1374). `_code_mask` blanks string literals and
    comments so structural scans ignore punctuation inside them. It was a
    per-character loop and the largest single cost in jm's test suite,
    about a fifth of a serial session's CPU. It now gives identical output
    (checked against the loop on 200,000 random inputs and every C file in
    the repo), with one fix: a lone backslash ending an unterminated
    literal made the mask one character longer than its text, which broke
    the offset promise every caller relies on.

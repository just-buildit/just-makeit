- **A plain `bytes` composer field takes the same values whatever its
    siblings declare (gh-2701).** The bytes coercer is generated once per
    source, so when any field was `coerce = "bit_pattern"` a plain
    `bytes = true` field beside it also took jm's `"0101"` string and an int
    sequence (each element stored as `v != 0`), both refused in a source
    with no bit_pattern field. It now takes a `bytes` and refuses anything
    else with the `TypeError` it raises in such a source. The c-face CLI's
    `--<field>` flag for a plain field reads `0`/`1` digits and exits 2
    naming the character on anything else, where it silently dropped it,
    so `01x1` read as `011`.

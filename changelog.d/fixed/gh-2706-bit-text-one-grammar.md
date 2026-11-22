- **A `bit_pattern` field's CLI flag reads the text its Python face reads
    (gh-2706).** jm's string form of a bit pattern, `0`/`1` digits or `0x`
    hex expanded MSB first, was read by the Python `str` coercer and by a
    separate loop in the c-face CLI that knew no hex, so `bits="0xA"` was
    four bits while `--bits 0xA` exited 2. Both faces now read text through
    one generated C function and word a refused character with one
    sentence naming its index and the reason: Python raises
    `ValueError: bits: character 2 is 'x', not 0 or 1 (...)`, and the CLI
    prints the same text after `bad --bits:` and exits 2.

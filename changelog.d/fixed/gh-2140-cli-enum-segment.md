- **A composer CLI reads an enum segment field as a choice, not a
    number** (gh-2140). The generated CLI's segment-field loop had no enum
    branch, so doppler's `gap_noise` shape (`type = "int"`,
    `enum = "gap_noise"`, `default = "auto"`) rendered
    `int gap_noise = (int)auto;`, which does not compile, and with a
    default that did compile, `--gap_noise off` read as the number `0`
    whatever choice was named. A source and a segment enum field now share
    one treatment: the flag starts at the default's C value and is looked
    up in the `[[enum]]` table, and an unknown choice prints
    `<prog>: --gap_noise: invalid choice 'x' (choices: auto, off)` and
    exits 2. An enum that binds `enumerators` now reaches C as its
    constant on the source field too, where the CLI had stored the
    choice's position.

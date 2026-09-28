- **The doppler-linked examples build against doppler 0.58** (`nco_tone`,
    and `kitchen_sink`'s `tone`). doppler 0.58.0 moved every header under
    `doppler/` and prefixed every C symbol with `dp_`, so both examples
    failed to compile against the release CI downloads (`nco/nco_core.h: No   such file or directory`), on main and on every PR. They now include
    `doppler/nco/nco_core.h` and call `dp_nco_*`, and the floor and offline
    fallback both move to 0.58.0, the first release with those spellings.

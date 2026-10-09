- **The types page says which element each array takes, and the
    configuration page what `apply` does to a generated test** (gh-2041,
    gh-2043). `docs/types.md` had one "array element" column for three
    slots that never shared a set, and said `bool`, `int` and
    `long double _Complex` could not be the element of a step `T[]`, which
    they can: each scaffolds, builds and passes. Its Supported types table
    now has a column per slot -- a step `T[]` takes every step type, an
    array parameter (and `--out-type`) the fixed-width numeric ones, a state
    `T[N]` those plus `int` and `long double _Complex` -- and the per-slot
    state table lists `bool`, which it had left out. `docs/configuration.md`
    said the generated tests are written once, so adding `example_value`
    later changes nothing; since gh-1489 the next `apply` rewrites the
    scaffolded Python test and benchmark while they carry `# jm:generated`,
    and only the C smoke test keeps its zero-seeded call. The page now says
    so, and links to who owns each file.

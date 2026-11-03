- **A manifest refusal is an exception a library caller can catch, naming
    every problem** (gh-2440). Several of `load`'s checks (templates,
    families, platforms, `why` keys, str hints, `retain`, ...) and a few in
    `apply` and `upgrade` printed each error to stderr and raised
    `SystemExit(1)`, so `just_makeit.load_manifest` on a bad manifest wrote
    to the caller's stderr and exited. They now raise `Refusal` carrying
    every deduplicated error (`.errors`), and nothing is printed until the
    CLI reports it: still one `error:` line per problem, still exit 1.
    `jm status` keeps its explanation when the internal replay refuses.

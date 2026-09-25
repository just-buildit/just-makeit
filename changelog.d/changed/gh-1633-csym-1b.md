- **Internal: every derived C symbol takes its stem from `_csym`** (gh-1591,
    phase 1b, gh-1633). The method and property families, the state
    helpers, the destructor, create-error and serializable glue, module
    functions, views, benches, docs lookups and status messages now all name
    a component's C identifiers through one stem, threaded as a required
    keyword so a missed caller fails loudly. Generated projects are
    byte-identical. The hand-spelled-symbol ratchet is strict at zero, and a
    new test renders a broad fixture under a stem override and reads the C:
    phase 2's `[project] c_prefix` is now a change to `_csym.stem` alone.

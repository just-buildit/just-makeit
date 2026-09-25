- **Internal: one owner of the derived C symbol stem** (gh-1591, phase 1 of
    3). Every C identifier jm derives from a component, module or function
    name -- `<comp>_create`, `<comp>_state_t`, the `<COMP>_CORE_H` guard, a
    method's `<comp>_<name>` -- now takes its stem from `_csym`, while the
    name stays the file stem; the C templates read a new `<<csym>>` slot,
    and a ratchet pins the Python sites still spelling one by hand (363 to 175).
    This is what lets phase 2's `[project] c_prefix` namespace a
    project's symbols in one place. Generated projects are byte-identical to
    before.

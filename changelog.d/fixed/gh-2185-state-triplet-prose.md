- **The `get_state` / `set_state` docstrings no longer say construction
    parameters never travel** (gh-2185, gh-2234). Every `serializable`
    object's `get_state` said "Construction parameters are not included",
    and its `set_state` said "the object keeps the parameters it was
    constructed with". That was on the runtime `__doc__` and both `.pyi`
    writers. Both claims are false for a core whose blob carries a setter's
    value as state, which `set_state` then replaces, and for one that
    writes configuration as reject keys and refuses a blob built
    differently. jm renders one text for every object and cannot tell
    which kind a core is, so the text now says what holds for all of them.
    It captures the state that evolves as the object runs, including any
    value a setter changed after construction. Construction parameters are
    not restored, so restore into an object built the same way. A core may
    check some of them and refuse a blob built differently. `apply`
    refreshes the text in an existing project, a module object's binding
    included. `tests/test_gh2185_state_triplet_prose.py` walks a generated
    project for every face of the two methods and refuses the old claims on
    each.

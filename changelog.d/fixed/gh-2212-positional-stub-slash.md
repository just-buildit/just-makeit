- **A handle, capsule or composer stub marks a positional-only call with
    `/`** (gh-2212). A handle method, factory or `set_state` whose binding
    takes no keywords had a stub that named its parameters plainly, so
    `write(iq=...)` type-checked and then raised `TypeError`; every capsule
    module function, and a composer's `execute`, `from_json`, `from_file`
    and `steps`, were the same. Each stub now ends its parameters with `/`
    exactly when its binding is registered without `METH_KEYWORDS`, asked
    of the predicate that picks the flags.

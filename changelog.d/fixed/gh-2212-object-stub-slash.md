- **An object's positional-only methods are stubbed with `/`** (gh-2212).
    An object's `step`, `set_<field>`, `__exit__`, `set_state` and every
    declared method that parses positionally register without
    `METH_KEYWORDS`, so `obj.step(x=1.0)` raises `TypeError`; their stubs
    named the parameters plainly, so a type checker accepted it. Both stub
    writers, standalone and module, now ask one function for each member's
    flags, read from the method table the same class's binding registers,
    and close the positional parameters with `/` exactly where those flags
    take no keywords. A module's `jm function`s are read from the module's
    own table the same way; every one that takes a parameter binds with
    keywords today, so their stubs are unchanged.

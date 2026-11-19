- **A module function returns its scalar `out` params (gh-2683).** A scalar
    param with `out = true` was bound as an INPUT: the prototype took it by
    value and Python had to pass it, so a lookup returning a pair had no
    binding. It is an output now: C takes `T *name`, Python does not pass
    it, and the function returns its value -- a tuple in declared order for
    several -- with the stub, `help()`, the contract test and the C smoke
    test agreeing. Accepted on a `void` function or with
    `check_return = true`, whose status raises before the outs come back;
    `jm function --out-param name:T` spells it.

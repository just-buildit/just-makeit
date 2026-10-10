- **A handle method's `.pyi` names its array argument as the manifest
    does** (gh-2207). The stub called it `x` whatever was declared, while the
    binding's keyword list and the runtime docstring used the declared name:
    for `send(iq, fs, fc)` the stub said `send(self, x, fs, fc)`, so
    `send(x=...)` raised `TypeError` and a type checker refused
    `send(iq=...)`, the call that works. A count-in method's stub did the same
    with `n`. The binding and the stub now take each parameter's name and
    position from one place, `_handle.py_args`.

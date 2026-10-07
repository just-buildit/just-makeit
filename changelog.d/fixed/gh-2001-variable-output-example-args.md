- **A params `variable_output` method's doc example passes every argument
    the binding takes** (gh-2001). Its synthesized example called the
    method with one argument whatever the params -- `obj.delay(np.zeros(4))`
    for `delay(x, mu)`, a `TypeError` the moment it runs. It is built by the
    one builder the other shapes' examples use: a typed array per array
    param, the type's zero per scalar, a real choice per enum. A lone array
    param now reads `np.zeros(4, dtype=...)`, so every such method's runtime
    `__doc__` changes once on the next `apply`.

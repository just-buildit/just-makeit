- **A `variable_output` method with an input AND params passes the params
    to its kernel** (gh-1960). The `_core.h` prototype, the `_core.c` stub
    and the binding all dropped them, while both `.pyi` writers and the
    runtime doc advertised them: `amp.run(x, 0.3)` raised `TypeError`, and
    `amp.run(x, mu=0.3)` was accepted and the `0.3` discarded. They now
    follow the input in all three
    (`const T *in, size_t n_in, <params>, T *out`), and are parsed as every
    other params shape's are, positional or keyword. The shape still takes
    no `out=` (gh-2028). In a tree scaffolded before this, `apply`
    re-declares a standalone object's prototype and regenerates its binding,
    so the build names the `_core.c` kernel that still lacks the params; a
    module object's sacred fragment keeps the old binding, and `apply` and
    `jm status` report it.

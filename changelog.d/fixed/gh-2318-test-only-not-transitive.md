- **A `test_only` dependency no longer leaks into another component's
    link line** (gh-2318). `{ name = "x", test_only = true }` kept `x_core`
    off its own component's core, but the transitive `depends_on` walk
    followed that edge for everyone else, so a component depending on this
    one linked `x_core` PUBLIC on its shipped core. Likewise, what `x`
    itself depended on went PUBLIC on the declaring component's core. The
    walk no longer follows a dependency's `test_only` edges, and a core's
    PUBLIC line drops every core reached only through a `test_only` edge.

- **A generator's, tick's or sink's `steps()` takes its arguments by keyword**
    (gh-1901). Unless a field was `controllable`, a void-arg object's
    `steps()` was bound positional-only, so `g.steps(n=4)` raised
    `TypeError: G.steps() takes no keyword arguments`, while both `.pyi`
    writers declared `steps(self, n: int = 1)` and its docstring read
    `steps(n=1)`. A scalar-to-void sink's `steps(x=a)` failed the same way,
    and its docstring advertised an `out` argument and an ndarray result
    that a sink does not have. Every `steps()` is now keyword-capable, as
    `docs/arguments.md` already said, standalone and in a module; positional
    calls are unchanged, and a sink's docstring reads `steps(x)`. `jm apply`
    regenerates the binding.

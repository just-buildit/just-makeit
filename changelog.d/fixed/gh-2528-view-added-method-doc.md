- **A view's `doc` on a method it adds is the whole docstring** (gh-2528).
    A view method declared with `fn` (or on the derived symbol) took its
    `doc` as the summary line only; the C function's header block -- shared
    with every other class binding that function -- still supplied the
    prose, Parameters, Returns and Examples. Now every view method's `doc`
    is its whole docstring on the `.pyi` and the runtime `__doc__`, as an
    override's already was (gh-2498); a view without one keeps the header's
    text, and a `doc` on a member sharing its C function with another member
    of the same view is refused.

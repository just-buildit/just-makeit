- **A view's `doc` for a member its parent has is that member's whole
    docstring, and the member stays the parent's** (gh-2498). A
    `[[<obj>.views.methods]]` entry naming a shared method rendered the
    view's `doc` followed by the parent header's prose, Parameters,
    Returns, Raises and a second Examples section; naming a built-in such
    as `reset` or a generated `<method>_max_out` accessor declared a NEW
    method, so `reset` became `reset() -> complex` and `jm apply` rewrote
    the header's `<stem>_reset()` declaration; and a view's own
    `error_message` was refused. Now an entry carrying only `name` with
    `doc` and/or `error_message` is an override of the parent's member:
    the view's text replaces the docstring on the `.pyi` and the runtime
    `__doc__` alike, the signature and C declaration stay the parent's,
    and `error_message` (binding text, not the C call) may differ per
    view. Anything that changes the C call is still refused without
    `--fn`. `jm script` now replays a method's `doc` (`--doc`), the
    parent's and a view's: it was dropped, so a replayed project lost
    every manifest method docstring.

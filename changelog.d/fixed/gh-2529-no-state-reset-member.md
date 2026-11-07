- **A view's `doc` override of `reset` on a `no_state` object is an
    override, not a new method** (gh-2529). jm's member derivation dropped
    `reset` under `no_state` while the binding still binds it, so the view
    entry rendered `reset() -> complex` with the parent's Example, `apply`
    rewrote the header's `<stem>_reset` declaration, and the view's fragment
    failed to compile (`void value not ignored`). `reset` is now a member
    exactly when the binding emits it -- unless `no_reset` -- so the view
    keeps `reset() -> None` with its own doc on both faces.

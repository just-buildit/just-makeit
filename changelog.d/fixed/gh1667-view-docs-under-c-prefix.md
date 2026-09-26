- **A view keeps its parent's docstrings under a `c_prefix`** (gh-1667).
    A view's `.pyi` members read the parent's header blocks re-keyed from
    the parent's name to the view's, and both ends were spelled from the raw
    name (`ddc_`) while the header declares the prefixed one
    (`dp_ddc_execute`) -- so every inherited method, property, struct-field
    comment and state-only `_max_out` fell back to its name stub ("Norm
    freq."), and `jm status --docs` reported them as gaps. Both ends now come
    from the C stem. A new gate renders the gh-1633 fixture, plus a view over
    every doc-bearing member shape, with authored prose in every sacred
    header, with and without a prefix, and requires the two to differ only by
    the prefix. A field property documented through a hand-written
    `<comp>_get_<prop>` that `jm upgrade` does not respell is gh-1670.

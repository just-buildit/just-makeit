- **A composer seam you name with the prefix is yours, not a collision**
    (gh-1694). `bridge_fn`, `bridge_error_fn` and a computed `fn` name C
    functions the project writes; jm derives none of them, but declares each
    in the module's `<cname>_bridge.h`. The `c_prefix` rename table read
    every header declaration that starts with a component's stem, so
    renaming `bridge_fn = "wfm_source_to_synth"` to `"dp_wfm_source_to_synth"`
    made it a rename of the bare name, and `apply` and `jm upgrade` refused
    the author's own definition as "already declares ... the name c_prefix
    derives from `wfm`". A name the seam header declares only because a key
    spells it is no longer read as derived there (`_csym._echoed`, from
    `_composer.seam_fns`, the one list the header renders). The same name
    declared where jm does derive it keeps its rename.

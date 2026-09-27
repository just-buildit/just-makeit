- **A container property's accessors take the `c_prefix` stem**
    (gh-1695). A `dict` / `list` / `tuple` property's count accessor was
    derived from the raw component name while its key and value accessors
    took the stem, so a prefixed project rendered `rc_num_stages` beside
    `dp_rc_stages_value`, and `jm upgrade` onto a prefix had no row for it:
    the name stayed bare and `apply` and `status --check` were clean. Once
    the author renamed the implementation, `apply` scaffolded a
    `return 0` placeholder beside it -- a count that reads as a valid empty
    list. Every accessor name now comes from one derivation,
    `_csym.container_accessors`, read by the header, the stub, the binding,
    the codec decode and the rename table, which also carries the accessors
    no render declares (a codec's `entry_fn` / `entry_type`, a view's
    container property). A tree an earlier jm left with the bare spelling
    is refused by `apply`, naming the file, until `jm upgrade` respells it;
    nothing is scaffolded beside it.

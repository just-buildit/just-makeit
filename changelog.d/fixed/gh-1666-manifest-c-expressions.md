- **`jm upgrade` respells the C expressions your manifest holds** (gh-1666).
    Moving onto a `c_prefix` respelled `*_impl` bodies and `type` keys but
    not the other manifest strings jm splices into generated C verbatim, so
    a module function's `out_size = "kaiser_num_taps(...) | 1"` survived
    the upgrade and the regenerated `_ext.c` called a function that no
    longer existed. `out_size`, a property's `expr`, a method's
    `count_default`, an object's `init_post_parse`, a handle
    `create_post`'s `arg` / `when`, and the free-form C type keys
    (`capsule_type`, `c_type`, `value_type`, `entry_type`, `state_type`,
    `struct`, `handle_type`, `record_dtype`) are now respelled in place, and
    `apply`'s existing-tree refusal names a stale one -- both read one
    declared set, `_csym.MANIFEST_C_KEYS`. Author-named keys (`fn`,
    `create_fn`, `out_len_fn`, ...) are still untouched.

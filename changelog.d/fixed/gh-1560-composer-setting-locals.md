- **Two composer settings named `x` and `x_set` compile** (gh-1560). Each
    setting's was-it-passed flag in the generated `tp_init` was `_st_<n>_set`,
    which is also the value local of a sibling named `<n>_set`, so the pair
    declared one local twice. The flag is now `_stset_<n>`, which no value
    local can spell. A composer that declares settings sees its generated
    `_ext.c` change on the next `apply`; nothing to do by hand.

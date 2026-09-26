- **`jm upgrade` respells a property getter jm does not declare** (gh-1670).
    A `field = true` property's docstring is read from `<stem>_get_<prop>`,
    which an author declares in the sacred header to document it -- but the
    `c_prefix` respell moved only the names jm's render declares, so
    `fir_get_num_taps` kept its bare spelling while the doc lookup asked for
    `dp_fir_get_num_taps`, and the stub fell back to "Num taps.". Every
    property's getter is now a derived name, declared or not: `jm upgrade`
    respells it and lists it in the rename table, and `apply` refuses a tree
    that still spells it bare.

- **A composer module's `_ext.c` builds clean under `-Wall -Wextra`**
    (gh-1863). Its `_enum_index` lookup and `_attach_bytes` coercer were
    emitted whatever the shape, so a composer with no enum to look up or no
    bytes field got `-Wunused-function` from generated glue. Each is now
    emitted only when the rest of its translation unit calls it, as the
    module and handle faces already did for the lookup (gh-1745). A
    hand-written `*_ext_extra.c` cannot rely on either. The `composer_seams`
    example now builds with `-Wall -Wextra -Werror`.

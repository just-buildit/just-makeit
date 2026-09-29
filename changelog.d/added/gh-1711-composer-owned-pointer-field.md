- **A composer source field can bind a host object into a pointer member**
    (gh-1711). An owned-pointer field names the pointed-to type with
    `object = "<comp>[.<Class>]"` (or `type`, `capsule` and `header`), an
    optional `c_ptr`, and four host functions that are required together:
    `copy_fn`, `free_fn`, `parse_fn` and `format_fn`. jm declares all four in
    the module's `_bridge.h`. The source owns a copy, never a reference: the
    constructor keyword and the setter take `None`, the host object (copied
    through `copy_fn`) or its text (read through `parse_fn`, and a refusal
    raises `ValueError`). The getter returns the text from `format_fn`.
    Dealloc frees the copy, and `Composer.segments` gives each rebuilt source
    its own copy. The generic `to_json` nests the text as JSON and
    `from_json` reads it back, and the c-face CLI takes `--<name> TEXT`. The
    `.pyi` types the keyword `FrameDesc | str | None` and the property
    `str | None`.

- **`jm upgrade` and `apply` read every spelling of the C a manifest
    holds** (gh-1684). A basic string with an escaped quote
    (`impl = "puts(\"x\"); lo_create(1);"`) was read only up to the first
    `\"`, so a derived name after it was neither respelled onto a new
    `c_prefix` nor named by `apply`'s refusal. It is now read whole, as
    TOML decodes it, and rewritten with your escapes kept. A `replace`
    table written with dotted keys (`replace."G(s)" = ...`,
    `lo.replace.N = ...`) or as an inline table spread over several lines
    (TOML 1.1, which jm reads on Python 3.9 and 3.10) is read too. The
    `_Complex` respell now leaves a `replace` key alone when its body comes
    from an `impl_file` that the respell does not rewrite, as the
    `c_prefix` respell already did.

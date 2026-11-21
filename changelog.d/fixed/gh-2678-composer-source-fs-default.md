- **A composer source type's `fs` runs at the default its docstring states**
    (gh-2678). The source type's constructor defaulted `fs` to `1e6`,
    written in jm, while its class docstring and `.pyi` borrowed the
    segment's `fs` field -- so doppler's `Synth` documented `default 1.0`
    and ran at 1e6. The default, the docstring entry and the `fs` getset
    doc now all come from one row: the source `object`'s own `fs` init
    param (its `default` and `doc`), or, when it declares none, jm's
    fallback `1e6`, documented as such. The segment's `fs` documents the
    segment. An object `fs` with no default is refused.

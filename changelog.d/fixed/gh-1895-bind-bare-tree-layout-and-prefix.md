- **`jm bind` with no `just-makeit.toml` binds a `jm new`-scaffolded
    header: it reads the header layout from where the header is, and the C
    prefix from the stem it declares** (gh-1895). With no manifest, `bind`
    looked for the header at the legacy `native/inc/<comp>/` path, compared
    its symbols with the bare name, and rendered the prefixed `#include`s:
    a header under `native/inc/<pkg>/` was "not found", one with the
    default `c_prefix` was "declares component 'p_g', but you asked for
    'g'", and one moved into the legacy path bound with an `#include` that
    does not resolve. One owner now answers the header's path, the
    symbol-stem check, its Doxygen and the render. When neither layout
    holds the header, or both do, `bind` exits 1 naming both paths and
    writes nothing.

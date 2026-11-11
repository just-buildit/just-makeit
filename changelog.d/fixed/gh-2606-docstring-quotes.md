- **A docstring whose text holds `"""` no longer breaks the `.pyi`**
    (gh-2606). jm writes a manifest `doc` or a header `@brief` between
    triple quotes as given, so a triple quote in the text ended the literal
    early and the stub did not parse (`apply` failed reading it back). The
    shared docstring layouts now escape it, and the one-line summary layout
    also escapes a final quote or backslash that would merge with the
    closing quotes, and doubles each backslash, so the docstring reads back
    as the text.

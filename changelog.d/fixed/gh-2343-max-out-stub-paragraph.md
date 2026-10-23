- **A module stub's `*_max_out` docstring keeps its wrapped paragraph**
    (gh-2343). The header Doxygen of a `*_max_out` was rendered into the
    module-aggregated `.pyi` with a blank line after every source line, so a
    six-line paragraph read as six one-line paragraphs. The runtime docstring
    and the standalone object's stub were already right. The module stub now
    takes the header block through `GlueMethod.with_header_block`, the same
    path the standalone stub uses, so the paragraph renders as consecutive
    lines.

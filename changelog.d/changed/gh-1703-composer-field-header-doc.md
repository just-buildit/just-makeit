- **A composer field's docstring is its struct member's Doxygen** (gh-1703).
    A `source.fields` / `segment.fields` row is a member of the struct the
    composer wraps (`source.struct`, `segment.struct`), so the member's doc in
    the header (a trailing `/**<` or a `/** */` block above it) is now the
    field's docstring on both faces, the `.pyi` and the runtime getset doc.
    The manifest `doc` is the fallback for a member with none. The lookup
    reads the headers the binding includes, and only the field's own struct:
    a same-named member of any other struct is never used (gh-1300).
    Where both exist and disagree, `jm status` reports it in the `DOC`
    section and `--check` fails, so a tree carrying restated field docs
    will see one finding per drifted field on upgrade. Delete the manifest
    `doc`, or move the better sentence onto the member.

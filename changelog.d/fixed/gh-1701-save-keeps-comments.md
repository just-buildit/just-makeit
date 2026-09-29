- **A mutating save keeps the comments on every row it did not change**
    (gh-1701). A table whose value changed was re-rendered whole, so every
    comment inside it was deleted: appending one method to each of
    doppler's objects deleted 422 comment lines across 60
    `objects/*.toml`, and editing one `doc` on one composer
    `source.fields` row deleted 15. A changed table array (`[[x]]` or an
    inline `x = [{...}]`) is now synced row by row, and a changed row key
    by key, in the manifest, in `objects/*.toml` and `modules/*.toml`
    fragments, in composer sub-tables and in `[[enum]]`. A comment above a
    row stays with that row: it is deleted with it, and it no longer ends up
    above a neighbour when a row is added or removed. A new key lands with
    its table's values rather than under the next table's comment.

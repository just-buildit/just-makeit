- **`status` no longer tells you to delete a binding that accepts more than
    the manifest declares** (gh-1447). ACTIONABLE advised "Delete the file
    and re-run `jm apply`" for every fragment whose calling convention
    differed from the manifest, including one whose binding was the
    superset -- doppler's `Resampler.execute_ctrl` (`"OO|O"` vs `"OO"`),
    where following the advice deletes a working `out=`. Those fragments
    now land in BINDING AHEAD, which says the manifest is behind, marks
    each member `(binding ahead)`, and points at declaring the argument in
    `just-makeit.toml` or keeping the file. The direction is
    `_adopt.binding_ahead`, the same predicate `adopt --check` refuses on.

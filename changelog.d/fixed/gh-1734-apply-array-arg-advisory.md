- **`apply` names a sacred fragment whose array arguments predate
    `jm_array_arg`** (gh-1734). gh-1700 routed every array-argument
    conversion through `jm_array_arg`, which refuses a `str`, and widened the
    `.pyi` to match; a sacred `_ext_<obj>.c` fragment rendered before it
    still calls a bare `PyArray_FROM_OTF`, so a `str` is silently parsed as a
    number while the stub says otherwise. `apply` and `jm status` warned
    about fragments lacking gh-1710's output-size guard but said nothing
    about these. Each such member is now reported the same way, with the
    consequence, so a project knows which fragments to delete and
    regenerate. The marker is the helper CALL in the masked body, so its
    name in a comment does not satisfy it and a GNU-formatted
    `jm_array_arg (` does.

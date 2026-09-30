- **An extension that takes no array builds clean under clang `-Werror`**
    (gh-1747). `jm_array_arg`, the array-argument converter every extension
    carries (gh-1700), is a `static inline` defined in the `_ext.c` itself.
    clang reports an uncalled one there as `-Wunused-function` (gcc does
    not), so a `--no-state --no-step` object with no method, a module
    whose functions take only scalars, or an extension whose every array
    argument declares a `str_hint` (so calls only `jm_array_arg_hint`),
    failed a clang `-Werror` build. The
    helper stays in every extension, because a module's `_ext.c` also
    compiles hand-patched fragments and `*_extra.c` hooks jm does not read,
    and it is now marked `unused`, which is what a `static inline` in a
    header already is to both compilers. Regenerating the `_ext.c` picks it
    up.

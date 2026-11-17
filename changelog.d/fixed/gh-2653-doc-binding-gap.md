- **An ordinary comment between a Doxygen block and its declaration no
    longer drops the docstring** (gh-2653). The binder skipped only
    whitespace, so a header made includable from C++ -- a doc block, then
    `#ifdef __cplusplus`, a plain `/* ... */` comment and the prototype --
    rendered its binding and `.pyi` undocumented, and `drift-check` went red
    with no hint why. jm now binds the way Doxygen does: whitespace, ordinary
    `/* */` and `//` comments and preprocessor directives are skipped, for
    functions and structs alike. A second doc comment still ends the
    binding, and so does a `#define`, which is documentable itself: a
    macro's block is no longer handed to the function declared after it.

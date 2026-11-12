- **A generated docstring reads back as its text** (gh-2620). jm writes
    docstrings around text it was handed, such as a manifest `doc`, a header
    `@brief` or a template slot, and some forty sites each wrote their own
    triple quotes. A backslash in that text was read as an escape: a `doc`
    of `C:\new` read back as a newline, and `\l` was a SyntaxWarning in the
    generated project. Only the numpy layout knew to write a raw string
    (gh-652). Most sites escaped nothing, so a triple quote in a handle
    field's doc or a template slot ended the literal early. Every generated
    docstring is now written by one function, and its value is exactly the
    text. A text holding a backslash is written as an `r"""` literal, unless
    it also holds something a raw literal cannot (a triple quote, a closing
    quote or backslash, a control character), which is escaped instead.
    Regenerated stubs whose docstrings hold a backslash change accordingly:
    run `jm apply`.

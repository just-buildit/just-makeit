- **A list-of-records method with a `--param` compiles and passes it**
    (gh-1961). A method returning a list of records (`result_fields`,
    neither `single` nor `variable_output`) declared the param in its
    prototype, and the binding never parsed it: the kernel was called
    without it, and so was it from the generated benchmark, so neither
    built. The params are now parsed as the single-record shape's are
    (gh-594), positional or keyword, with the input first, and the
    benchmark passes them; the runtime doc's example passes them too.

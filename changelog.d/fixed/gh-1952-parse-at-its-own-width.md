- **No generated binding parses a scalar into a variable narrower than PyArg
    writes** (gh-1952). Five faces parsed a scalar straight into its
    declared C type: every `kind = "handle"` shape (create args, factory
    params, method args, writable properties), a `kind = "capsule"` create
    param and writable property, a controllable `step()` / `steps()`
    override, and a codec method's fixed param. PyArg writes the width of
    its format char, so an `int8_t` or `bool` took the four bytes `i` / `p`
    write and a `float _Complex` took a 16-byte `Py_complex`. Each was a
    write past a local on the stack. A handle's `out_len_fn` methods parsed
    into a wider local, but seeded a complex one with `= 0`, which does not
    compile. Every one of these faces now parses into the row's wider local
    through the one tuple-parse slot (`scalar_arg_c` / `scalar_narrow_c`),
    seeds a default the way every other face does, and range-checks the
    value before narrowing it (gh-2144). A handle or capsule scalar local is
    now initialised, so `double fs;` reads `double fs = 0.0;`.
    `tests/test_gh1952_parse_width.py` reads every `PyArg_Parse*` call in a
    render of every face. It refuses any scalar parsed into a variable of
    another type than its format char writes, with the char-to-type map
    derived from `_CTYPE_META`.

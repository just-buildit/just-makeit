- **A `--param x` beside an `--arg-type` input is refused (gh-2027).** A
    method reads its input as `x`, so a param of that name, or one of its
    derived locals (`x_len`, `x_obj`, `x_arr`, `x_raw`), declared it twice:
    the keyword table listed `x` twice and `_core.h` redeclared it. gh-1512's
    collision rule never asked, because the input is not a param. It does
    now (`_builtins.input_locals`), for `jm method` and `apply` alike, before
    anything is written; an `arg_type = "void"` method keeps its `x`.

- **`const char *` is refused as an object's step() type, as the docs always
    said** (gh-1884). Every face accepted it and exited 0 on a broken
    scaffold: `jm new --object cc --arg-type 'const char *'` failed its own
    `make test`, `jm object --preset generator --return-type 'const char *'`
    segfaulted the generated suite, and a consumer (or a `'const char *[]'`
    input) passed each slot's `PyObject *` of an `NPY_OBJECT` array to C as
    a string. `--arg-type` / `--return-type` on `jm new` and `jm object`
    (every `--preset`, and a module object) now refuse it as a scalar and as
    a `T[]` element with one `error:` line naming the flag and what to use
    instead (an init param or a method param); `apply` and `status` refuse
    a manifest `arg_type` / `return_type` the same way before anything is
    written; and the render refuses it for the paths that do not pass
    through `apply`, a mutating command over a hand-edited manifest and
    `jm bind` of a header. The refusal is keyed on the type registry's
    `kind`, so a string type registered later is refused too; an init
    param, and a method's or function's param or return, keep their
    string support.

- **dtype dispatch beside a defaulted array compiles** (gh-1825). An object
    whose `real_type` / `real_create_fn` array was followed by a
    `default = "[]"` array did not build, standalone or in a module: the
    dispatch called its constructor inside its own block, before the later
    array declared `<name>_arr` / `<name>_len`. The dispatched array is now
    acquired like every other one, and the constructor call comes after all
    of them. Two per-call constructor choices on one object (two dispatched
    arrays, or one beside an `optional` array) and a 2-D dispatched array
    are refused when the manifest is read; each generated C that overwrote
    the first constructor's result or did not compile.

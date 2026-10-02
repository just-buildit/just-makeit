- **A scaffold declares and stubs every constructor its binding calls**
    (gh-1827). An `optional` array's `create_fn` and a dtype dispatch's
    `real_create_fn` were called by the generated binding but neither
    declared in `<comp>_core.h` nor stubbed in `<comp>_core.c`, so an
    untouched tree failed on an implicit declaration. The scaffold now writes
    a prototype (with its `@param` docs) beside `create()` and a stub body
    after it, both from the argument list the binding passes, on both
    faces and under `header_only`. Declared later in the manifest, `apply`
    adds the missing stub, as it does for a method (gh-1294).

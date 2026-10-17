- **The generated C benchmark calls every method as its kernel is
    declared.** `bench_<comp>_core.c` spelled each shape's call by hand,
    beside the prototype in `_core.h`. A `single` record method with an
    input was called with a scalar, `f(obj, 0.0f)`, while its kernel takes
    `const float *in` and `size_t n_in`, so the tree did not build. The
    call is now read off the kernel's own declaration, argument for
    argument, and a kernel taking a block input is handed a buffer.

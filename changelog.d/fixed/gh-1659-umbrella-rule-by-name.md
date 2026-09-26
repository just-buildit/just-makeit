- **A header you wrote beside the umbrella is yours, not jm's** (gh-1659).
    jm recognised its umbrella header by `native/inc/*.h`, and that glob's
    `*` crossed `/`, so every header under `native/inc/` -- in either
    layout -- read as the umbrella `apply` rewrites. Under a new
    `c_prefix`, `apply`'s refusal then never named such a header that still
    called an unprefixed name, while the same code in a `.c` file was
    named. The umbrella is now matched by its name (`native/inc/<pkg>.h`,
    or `native/inc/<pkg>/<pkg>.h`), and no ownership rule's `*` crosses a
    directory any more, so a nested author file such as
    `native/src/<comp>/vendor/x_ext.c` is not taken for jm's binding either.

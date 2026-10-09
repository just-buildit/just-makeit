- **A blockwise object with different input and output element types passes
    its own generated tests** (gh-2133). The scaffolded `test_steps_runs`
    asserted the output's dtype was the INPUT element's, which held only
    while the two matched, so `docs/templates/blockwise.md`'s own example
    (`--arg-type 'int16_t[]' --return-type 'float[]'`) failed `make test`
    out of the box. It now asserts the dtype of the declared `--return-type`,
    the one the binding allocates. The generated C test, the `.pyi` and the
    runtime docstring already read each side from its own declaration.

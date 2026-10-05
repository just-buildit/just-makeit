- **The bundled example READMEs and a new project's README match what jm
    0.98.1 generates.** Every walkthrough was re-run against a fresh
    scaffold: C symbols carry the `c_prefix` and headers live under
    `native/inc/<pkg>/`, tables land in `objects/` / `modules/` fragments,
    `--variable-output` arrays are allocated per call (gh-604), and the
    `.pyi` doctest commands work as shown (`PYTHONPATH=src`) and say what
    runs them: a generated `make test` does not. Steps that could not work
    as written now do -- `cmake ... -q`, a missing `--return-type`, a
    `--pytest` flag `object` never had, a doppler floor below the example's
    own, an `example_value` only the test set, a `cd` in the wrong step --
    and array_processing, filter_module, full_workflow and varargs_method now
    build the C their READMEs show. A new project's README no longer says
    `pip install -e .` builds the extension or `make test` runs pytest when
    it runs unittest, and a `--build-system make` project's README describes
    its own build -- no `make docs`, CMake or Windows build it cannot run --
    from backend slots in the one README template.

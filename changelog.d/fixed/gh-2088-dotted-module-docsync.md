- **`jm apply` refreshes the runtime docs in a dotted module's binding
    fragments** (gh-2088). The doc sync built each fragment's path from the
    module id, `native/src/dsp.filt/dsp.filt_ext_o.c`, a file that never
    exists, so every fragment of a `--module dsp.filt` was skipped as
    missing: a manifest `doc` edit or a header Doxygen edit reached the
    `.pyi` but never the extension's `__doc__`, and `apply` said nothing.
    The path is now the module's cname, `native/src/dsp_filt/`, as every
    other reader spells it. The refusal for a module key set in two places
    names the fragment file that holds it, `modules/dsp_filt.toml`, rather
    than one that does not exist.

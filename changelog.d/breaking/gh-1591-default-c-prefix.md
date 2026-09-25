- **`jm new` prefixes a new project's C symbols with its package name**
    (gh-1591). `jm new dsp` now writes `[project] c_prefix = "dsp"`, so
    `fir_create` is `dsp_fir_create`, `fir_state_t` is `dsp_fir_state_t` and
    the guard is `DSP_FIR_CORE_H` from the first file: two installed jm
    packages that share a component name link and include together.
    `--c-prefix P` picks a shorter one; `--no-c-prefix` keeps the bare
    names. **Existing projects are untouched** -- no key, same names, until
    you set one and run `jm upgrade`. Breaking for a workflow that scripts
    `jm new` and then writes C against the derived names (the bundled
    examples did): that C now calls `dsp_<comp>_*`. The Python names, file
    names and anything you named yourself (`fn =`, `create_fn`, your
    macros) do not change. `jm script` of a project without a key now
    replays it with `--no-c-prefix`.

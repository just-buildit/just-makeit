- **`[project] c_prefix`: two installed packages may share a component
    name** (gh-1591, phase 2). Set with `jm new --c-prefix dp`, it prefixes
    every C symbol jm derives -- `fir_create` becomes `dp_fir_create`, the
    `fir_state_t` type `dp_fir_state_t`, the `FIR_CORE_H` guard
    `DP_FIR_CORE_H`, a module function `mix` becomes `dp_mix` in C -- and
    nothing you named: a manifest `fn =` / `create_fn` / `type_name`, your
    own macros in the sacred header, the Python names, the file names. A
    name that already starts with the prefix keeps it once (`dp_tlm` stays
    `dp_tlm_create`). `apply` refuses two names that derive one symbol, and
    refuses the key on an existing project whose C still spells the
    unprefixed names, naming each file, until `jm upgrade` respells it.
    Without the key nothing changes.

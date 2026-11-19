- **A method names its capacity function: `max_out_fn` (gh-2680).** A
    `variable_output` method bound with `fn = X` sized its output by a
    derived `X_max_out`, and nothing could name another -- so a status twin
    (`fn = "dp_ofdm_modulate_why"`) beside its plain form got a
    `dp_ofdm_modulate_why_max_out` called, declared and stubbed that only an
    alias could satisfy. `max_out_fn = "dp_ofdm_modulate_max_out"` (or
    `jm method --max-out-fn`) names it, on an object's, a module object's
    or a view's method: the binding calls it as written, its header arity
    and header block are read like a derived one's, and jm writes no second
    definition of one the tree already has. The Python `<name>_max_out`
    keeps its name. The derivation is now one function every face asks.

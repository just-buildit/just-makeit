- **A `--batch` method refuses every key its binding does not read**
    (gh-2261). A batch method is rendered ahead of every other result
    shape, so `pass_capacity`, `exact_max_out`, `max_out`, `out_divisor`,
    `status_return`, `error_negative`, a `--param` (gh-2021), `out_type`
    (gh-1991), `variable_output` (gh-2200, which did not compile) and
    `none_on_empty` (gh-2202) were each stored and generated nothing, and a
    record return crashed with a bare `KeyError`. One allow-list,
    `_keys.BATCH_METHOD_KEYS`, now answers all of them: any other key is
    refused before anything is written, by `jm method` and `apply` alike,
    naming the key and what a batch method reads, and a record return is
    refused as not a scalar. The per-key batch refusals it replaces
    (`multi_output`, the count keys, `out_cols`, `short_out`, `no_count`)
    are gone.

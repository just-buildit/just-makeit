- **A count method's refusal can pick its exception per cause** (gh-2584).
    A count has one refusal value, so `error_sentinel`, `error_negative` and
    `error_on_empty` raised one declared class whatever the cause: an
    argument the kernel rejects and a scratch `malloc` that failed both read
    `ValueError`. `status_out = true` (`jm method --status-out`) gives the
    kernel an `int *status` out-param, before `why` when both are declared;
    the binding initialises it to 0 and reads it only when the declared
    refusal fires, and the method's own `status_errors` rows map the code to
    a class -- `DP_ERR_MEMORY` to `MemoryError`. A code no row names raises
    the declared `error` / `error_message` as before, and `why` still
    supplies the sentence. It covers every count path (`out=` and
    allocating, `multi_output`, `record_dtype`, `pass_capacity`, the `out=`
    param count, a scalar `error_negative`, under `nogil` too) on an
    object's, a module object's and a view's method; the header, the stub,
    the bench, the `.pyi` `Raises` rows and `jm script` agree. Refused at
    load: no count refusal, beside `status_return` (and so on a
    `kind = "handle"` method, whose refusal is its returned status), a
    value that is not `true`/`false`, and beside a parameter named
    `status`. A module object's header lacking the parameter is `jm status`'s
    `WHY` finding and an `apply` warning, as for `why`. The parameter lands
    through `_diagnostics.append_tail`, one call over gh-2574's
    `append_why`, and the rows render through `_borrow.status_switch_c`.

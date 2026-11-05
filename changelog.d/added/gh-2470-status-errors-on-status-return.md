- **A returned status and a rejected state blob can name their exception**
    (gh-2470). A method with `status_return = true` raised
    `ValueError("<name> failed (rc=N)")` for every non-zero status, and a
    `serializable` object's generated `set_state` raised
    `ValueError("set_state rejected the blob")` for every refusal, so an
    out-of-memory code reached Python as bad input. The `status_errors` table
    a borrow (gh-1418) and a `check_return` function (gh-1614) already carry
    is now accepted on an object or view method with `status_return = true`
    (`jm method --status-return --status-error DP_ERR_MEMORY:MemoryError`),
    on a `kind = "handle"` method whose `int` return is a status, and on the
    `exit` finalizer's `__exit__`, which raises what the finalizer raises. A
    serializable `set_state` takes the same rows as `set_state_status_errors`,
    on the object or on a handle module
    (`--set-state-status-error STATUS:ExcName[:message]` on `jm object`).
    Every face renders
    through one switch (`_borrow.status_switch_c`) and one validator
    (`_borrow.rows_why_not`). A status with no row keeps today's exception,
    a project with no table renders byte for byte as before, and the `.pyi`
    and runtime `Raises` sections list each declared class. `jm script`
    replays both keys.

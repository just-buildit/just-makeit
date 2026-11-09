- **A method's refusal can raise the reason its C function names**
    (gh-2574, gh-2407). A method told its refusal causes apart only by
    return code, so one `error_message` had to list every rule. `why = true`
    (`jm method --why`) on `[[<obj>.methods]]`, a view's methods or a
    `kind = "handle"` module's `[[module.X.methods]]` is gh-1706's `why` on a
    method: the kernel takes a trailing `const char **why` after every other
    parameter, the binding passes the address of a local initialised to
    `NULL`, and the method's declared refusal raises its exception with the
    sentence the kernel wrote, or today's message when it wrote none. Every
    refusal a method can declare carries it -- `status_return` and its
    `status_errors` rows, `error_negative`, a count's `error_on_empty` /
    `error_sentinel` / signed `error_negative` on both call paths, the
    `out=` count, a `borrow`'s NULL and the `exit` finalizer `__exit__`
    calls -- under `nogil` too, where the sentence is read after the GIL is
    back. The parameter is C only; the header prototype, the `_core.c` stub,
    the bench and the `Raises` sections agree, and `jm script` replays the
    flag. Refused at load: `why` on a method with no refusal, a value that
    is not `true`/`false`, and beside a parameter named `why`. A module
    object's header that lacks the parameter is a `jm status` finding,
    `WHY`, and `apply` warns for each such method, naming the remedy. The one place the parameter lands is `_diagnostics.append_why`,
    for the prototype, the stub and every kernel call alike.

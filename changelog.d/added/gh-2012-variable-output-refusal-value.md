- **A `variable_output` kernel whose zero is a real answer can refuse:
    `count_type` + `error_negative`, or `error_sentinel`** (gh-2012). The
    kernel's one return value is the count, and its only declared failure
    was a zero (`error_on_empty`), so a resampler that legitimately emits
    nothing for a block had no way to refuse a call: `error_negative` was
    refused on the shape, and a `(size_t)-1` came back as a `RuntimeError`
    from the overflow guard, blaming the kernel for writing
    18446744073709551615 elements. Two opt-in forms now raise `error` with
    `error_message`, on the `out=` path and the allocate path, before the
    count meets that guard, and release what each path holds. A signed
    `count_type` (`"int64_t"`, any signed integer) with `error_negative`
    makes the kernel return its count or a negative code, raised with
    `(rc=N)` appended; `error_sentinel` (a verbatim C constant such as
    `SIZE_MAX`) names the value a `size_t` count refuses with. `0` stays an
    empty array unless `error_on_empty` or `none_on_empty` also reads it.
    The `_core.h` declaration, the `_core.c` stub and the binding all return
    `count_type` (the default `size_t` renders as before), the docstring and
    `.pyi` `Raises` document each condition, `apply` and `jm script` carry
    both keys (`--count-type`, `--error-sentinel`), the `c_prefix` respell
    of `jm upgrade` reaches the sentinel, and a sacred module fragment
    rendered before the declaration is reported by `apply` and `jm status`
    rather than left to raise the guard's error. Every combination that
    could not take effect is refused by `jm method` and `apply` alike:
    `error_negative` over a `size_t` count, a signed count without it, a
    sentinel on a signed count, both forms together, and either key (or
    `error_on_empty`) on a method whose binding never reads a count
    (`--batch`, `--varargs`, a codec, or no `variable_output`).

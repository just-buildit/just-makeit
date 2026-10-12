- **A header's `@param in` documents `x` on a method that takes `out=`**
    (gh-2028). The header names the block input by its C name, and the doc
    matched it to the Python `x` by position among the arguments no
    `@param` named -- counting jm's own `count` and `out` among them. So
    every input-only `variable_output` method, which has taken `out=` since
    gh-219, documented `x` as `Input.` unless the header also spelled
    `@param out`, on both `.pyi` faces and the runtime `__doc__`. The
    binding's own arguments now take no positional slot: an authored
    `@param out` or `@param count` still documents one by name, and jm's
    default text the rest. So a generator's `count` is no longer filled by
    position from a `@param` of another name (its C `n`); `@param count`
    documents it, as gh-1042 established.

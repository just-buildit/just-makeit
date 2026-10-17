- **A method's docs offer `out=` only when it takes one** (gh-2222). A
    `multi_output` method's runtime `__doc__` ended "Pass out= to write
    into your own buffer instead.", and a `multi_output` generator's
    `count` was described, on every face, as sizing an `out=` buffer with
    a `_max_out()` -- that shape has neither. A structured (`record_dtype`)
    result, which does take `out=`, never said so in its summary, and a
    count-driven generator's runtime signature line read `run(count=1)`
    above a binding taking `out=`. Each now reads `_outbuf`'s predicate,
    the one the binding and both `.pyi` writers already use.

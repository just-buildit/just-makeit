- **A `variable_output` method with params and no input parses them as
    every other method does** (gh-2023). That shape parsed its params with
    its own copy of the method parse, which knew scalars and plain arrays
    only. A `path` param did not compile, and an `enum` param was parsed as
    an `int` while both stubs said `str`, so the choice string the stub
    advertised was refused. A `bytes` or `capsule` param, a record-row
    array and `strict` never reached it either. The params now go through
    the method parse, with `out=` as its trailing argument, as the shape
    with an input has since gh-1960.

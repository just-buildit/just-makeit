- **A `why` switch that is not `true`/`false` is refused at load**
    (gh-1722). `[module.X.json] from_json_why` / `from_file_why` and a
    module function's `why` mark an existing function as taking a trailing
    `const char **why`. A function name, the natural guess, was accepted as
    truthy and rendered a two-argument call to a one-argument reader, which
    failed in the C compiler. Every command now refuses it, naming the key and
    where the function name goes. The `@param why` line jm adds to an authored
    docblock now says what the parameter means instead of being blank.

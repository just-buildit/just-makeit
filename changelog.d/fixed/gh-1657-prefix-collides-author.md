- **A `c_prefix` whose derived name your C already declares is refused**
    (gh-1657). With `c_prefix = "dp"`, a component `syncword`'s method `find`
    derives `dp_syncword_find`; a project that already wrote its own
    `dp_syncword_find` got two different functions under one name, and
    `jm upgrade` then respelled a wrapper's call to `syncword_find` into a
    call to ITSELF -- as a plain `.c` function that compiles, silently, into
    unbounded recursion. `apply` and `upgrade` now refuse it before writing
    anything, naming the file and line, the name, and the component and
    method (or module function) it derives from. A declaration in the files
    jm itself declares the name in does not count, so a tree an older jm
    partly moved onto the prefix still upgrades.

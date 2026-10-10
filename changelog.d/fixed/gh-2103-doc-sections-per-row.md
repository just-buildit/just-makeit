- **`jm status --check` no longer reports a sectioned `doc` as duplicated
    on a row where jm generates no section beside it** (gh-2103). Four
    tables decide row by row whether a docstring gets jm's numpy
    sections: a module function (none without header Doxygen and without a
    documented param), a handle method (none with no params and a `None`
    return), a handle factory (none without `init_params`) and a
    codec-pack method (never). On the rows that get none, a `doc` carrying
    its own `Notes` or `Parameters` block is the whole docstring, yet
    `status` called it "duplicated" and failed `--check`. The check now asks
    each row's own renderer what it writes, with the project's headers in
    reach, and reports only the rows where jm's section and the author's
    would both appear.

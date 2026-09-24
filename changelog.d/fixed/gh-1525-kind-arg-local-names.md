- **Handle, capsule and composer args named after a generated C local are
    refused** (gh-1525). The param-name rule from gh-1512 now covers the
    three module kinds too. Before, a handle `create_args` entry named
    `kwlist` or a method arg named `self` produced a binding that redeclared
    the name and did not compile, and `jm apply` still exited 0. `jm apply`
    now refuses such a name before it writes anything, and the error names
    the arg and the local it collides with. The refused names are the
    shared ones (`self`, `args`, `kwds`, `kwlist`, anything starting with
    `_`, an array arg's `x_obj` / `x_arr` / `x_raw` / `x_len`) plus the
    locals each kind's own wrapper declares: a capsule's `mod`, `w` and
    `cap`; a handle method's result `r` and its array locals (`n_in`,
    `in_data`, `out_data`, `view`, ...); a composer source field named
    `fs`; and a serializer param named `segs`. The shared names are refused
    on every kind, even where one wrapper happens not to declare them, so
    a rename is the fix wherever the error appears.

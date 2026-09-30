- **A `--no-state` object's benchmark builds** (gh-1742). Its scaffolded
    `bench_<obj>_core.c` wrote the `create()` call only as a TODO comment,
    but any method it then timed still passed `obj`, so
    `jm object X --no-state --no-step ...` followed by `jm method X ...` made
    a tree whose plain `make` failed with `'obj' undeclared`. A no-state
    object's constructor is `create(void)`, so the benchmark now creates and
    destroys `obj` like every other one (the same repair gh-181 made for
    `--no-step`). A new sweep builds every `--no-state` / `--no-step` shape,
    with and without a method, to hold it.

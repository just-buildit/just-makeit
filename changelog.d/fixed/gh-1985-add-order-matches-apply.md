- **`jm object`, `jm module` and the other commands that add to the root
    `CMakeLists.txt` and the umbrella header now write them in the order
    `jm apply` does, so `status --check` stays green after them** (gh-1985).
    `apply` lists components in manifest order; the commands listed them in
    the order they ran, a new block at the top and a new include at the
    bottom. The two differ in a fragment layout, whose files load sorted by
    name (`jm object q && jm object o` left both files STALE), whenever a
    standalone object followed a module object, and when a new block landed
    above a `c_deps` entry. `jm apply --only` had the same split, and also
    wrote its include without the blank line a full `apply` writes.
    `migrate-to-fragments` and `split-objects` change the manifest's order,
    so they now re-sort the two files to it instead of leaving them for the
    next `apply`. A project already in sync is unchanged: the order is the
    one `apply` has always written.

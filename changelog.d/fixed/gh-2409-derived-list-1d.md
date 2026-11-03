- **`derived = ["nx"]` names a 1-D array init-param's length** (gh-2409).
    gh-1097 made `derived` a list for a 2-D array's extents, and only the
    2-D path read the list. On a 1-D array, an object's or a view's,
    `jm apply` crashed with an `AttributeError` because the prototype said
    `size_t ['nx']`. A one-name list now means what the bare string means.
    A list naming more extents than the array has is refused, naming the
    param. `jm script` used to write such a param as `derived:['nx']` and
    now writes `derived:nx`.

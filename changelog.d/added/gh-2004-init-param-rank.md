- **`rank` and `elements_per_sample` on a constructor array:
    `[[<obj>.init_params]]`** (gh-2004). gh-805 §C's two array-shape keys
    were a method's and a module function's only, so a constructor array
    whose C contract is 1-D silently flattened a 2-D input through
    `PyArray_SIZE`: `HalfbandDecimator(np.zeros((4, 19)))` constructed, and
    `jm adopt` dropped the hand-written guard that had refused it, because no
    key could carry it. Written anyway, either key drew an unknown-key warning
    and did nothing. Now `rank = 1` on an init param emits the method form's
    guard, `ValueError: h must be a 1-D array`, on every constructor path that
    acquires an array (plain, after another array, dtype dispatch, defaulted
    `"[]"`, optional dispatch, a view's own constructor, a module object's
    fragment); it fails as a `tp_init` must, `return -1`, and releases every
    array already held. `elements_per_sample = N` divides the length
    `create()` receives into samples. Both are TOML-only, like their method
    spelling, and `jm script` names them in a NOTE. `load` now refuses
    either key where nothing would read it: on a param that is not an array
    (on a method or module-function param too, where it was silently
    ignored), a value that is not an integer of at least 1 (`rank = 0` was
    read as no guard), and on an init param a `rank` a `T[][]` contradicts or
    an `elements_per_sample` above 1 on a `T[][]` or a dtype-dispatch array.
    An undeclared key renders exactly what it did.

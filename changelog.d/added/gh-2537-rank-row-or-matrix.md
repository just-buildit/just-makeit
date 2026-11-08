- **`rank = [1, 2]`: a `T[][]` array that also takes a 1-D one, as one row**
    (gh-2537). A constructor's `T[][]` init-param refused any array that was
    not 2-D, so a `Correlator(ref)` taking a 1-D reference or a 2-D template
    through one `(ref, ny, nx)` contract forced `ref[None, :]` on its
    commonest call. Now `rank = [1, 2]` -- the one list `rank` may be, and
    only on a `T[][]` -- reads a 1-D array as `dim0 = 1`, `dim1` its length,
    a 2-D one as before, and refuses any other rank with a `ValueError`
    naming both; the C signature does not change. It holds on the required
    and the optional-dispatch paths, whose rank refusal now also releases
    every array the constructor already holds. The `.pyi` says what a 1-D
    input means. The list is an init param's only: any other list, the list
    on a 1-D type, and the list on a method's or a module function's param
    (whose `T[][]` passes a flat length, gh-1908) are refused at load.

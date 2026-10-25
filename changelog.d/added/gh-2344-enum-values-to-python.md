- **An `[[enum]]`'s values are published to Python, once** (gh-2344). An
    object's extension module now carries one attribute per enum its bindings
    index, named by the enum in upper case: `from proj.eng import ENG_STATE`.
    A dense enum is a tuple, so `ENG_STATE[2]` is the name of C value 2. An
    enum declared with `enumerators` that are not literally `0..n-1` is a dict
    from each C constant to its name. The values come from the same list the
    binding decodes through, and the `.pyi` declares the attribute, so a
    struct-sequence field or a telemetry value read back from a file decodes
    without restating the enum, and a reordered enum moves every reader with
    it. A module that shares an enum across objects publishes it once. A
    module object publishes only what its binding fragment defines a table
    for. Views, module functions and handle, capsule and composer modules
    are not covered yet (gh-2369).

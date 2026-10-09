- **The element contract passes on a first declaration of a `double` or
    `double _Complex` element** (gh-2067). Its refusal half hands the writer
    a dtype the binding must refuse, and chose it per C spelling: `int8` for
    `double`, `float64` for everything else. Both cast safely into some
    element (`int8` into `float64`, `float64` into `complex128`), so the
    binding accepted the array and `test_<writer>_speaks_<element>` failed
    with `DID NOT RAISE` on a project nobody had edited. The choice is now
    one rule over the element's numpy kind, the property safe casting is
    decided by: `complex128` for a real element, a string dtype for a
    complex one, neither of which numpy casts into the element on any
    platform.

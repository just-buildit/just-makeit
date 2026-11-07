- **A warning whose condition is a C expression is documented by the
    property that reads it** (gh-2534). Since gh-601 a `condition` may be an
    expression, and the class docstring's `Warns` entry printed it verbatim,
    so a Python caller read `self->handle->search->underpowered`. When a
    property on the same class -- object, module object or view, as that
    class binds them -- has an `expr` equal to the condition, ignoring
    whitespace that cannot separate tokens and enclosing parentheses, both
    faces (`__doc__` and the `.pyi`) now name that property. An expression
    no property reads is shown as before; the emitted C is unchanged.

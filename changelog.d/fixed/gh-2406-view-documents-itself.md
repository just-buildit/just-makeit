- **A view documents itself on both faces, not its parent or a synthetic
    name** (gh-2406, gh-2408). A view's `.pyi` was rendered from a copy of
    its parent's manifest table, while its binding reads several keys from
    the view. A setter's Raises in the stub named
    `<stem>__view_<v>_set_<prop>`, a function no header declares, where the
    runtime names the parent's setter it calls. The stub's class Raises
    quoted the parent's `create_error_message` and listed the parent's
    warnings, and it advertised the parent's `stream()`, which a view does
    not have. The runtime class docstring had no Raises or Warns at all, and
    a view's `__enter__` / `__exit__` named the parent's class. Both faces
    now read the view's own create error, warnings and class name, and the
    stub spells the C stem the binding calls.

- **A view's class may live in another module** (gh-2516). A
    `[[<obj>.views]]` entry takes `module = "<id>"`, written by
    `jm view <obj> <Class> --module <id>` when `<id>` is not the object's own
    module. The object's C core stays where it is: the view's `create_fn`
    stub is still scaffolded into the object's sacred core, and nothing is
    generated for the view's core. The class moves: its fragment is the
    other module's (`<cname>_ext_<view>.c`, included by that module's
    aggregator), its `__module__` and `.pyi` and `__init__.py` re-export are
    that module's, and that module's extension links the object's core with
    everything it links. So one class can keep the module its users import
    it from while its C core becomes another component's. Every reader of
    views (`apply`, `status`, `script`, `remove`, the link-check table,
    `process_global`'s reach) asks `_config.view_module`; a view without the
    key renders byte-identically.

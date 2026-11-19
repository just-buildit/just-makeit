- **A view's constructor can live in the view's own module** (gh-2681).
    `jm view --create-core <object>` (manifest `create_core`) names an object
    of the module the view's class lives in, whose core then declares and
    defines the view's `create_fn`. Before, it was always scaffolded into
    the parent's core, so a view in an application module over a general
    component put the application's constructor in the general header and
    its body in a core calling back into the module that depends on it;
    declaring it where it belongs was refused as a `c_prefix` collision.
    The named object must already `depends_on` the parent with
    `link = true`; `jm view` refuses it otherwise, naming the line to add.

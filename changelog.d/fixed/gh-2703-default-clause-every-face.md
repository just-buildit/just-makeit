- **Every parameter with a default names it in its docstring, on every kind
    (gh-2703).** gh-2679 added the `name : T, default <value>` clause to a
    method's and a module function's parameters. These still had none, on
    the `.pyi` and on the runtime `__doc__`:

    - a defaulted array param (`default = "[]"`). It now reads
        `default None`: an omitted array and `None` both reach C as `NULL`.
        An array init param used to read `default ...`.
    - a handle method's `args`.
    - a composer serializer's `params`, which had no `Parameters` section
        at all.

    A handle's `create_args` and a composer's fields kept their own copy
    of the clause. Every writer now appends it through one helper, and a
    numeric default is spelled as a method parameter's is: a handle
    `bool` reads `default True`, no longer `default true`.

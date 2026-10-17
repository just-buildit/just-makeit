- **A handle method arg or factory param declaring `enum` takes the
    choice string** (gh-2228). The binding passed `_arg_<name>`, the local
    a create-arg's validation declares, but no method shape or factory
    declared it, so the module did not compile; a method also parsed the
    arg as its `int`. Every method shape and every factory now parses the
    choice string and validates it into its int through the one enum
    emitter a create-arg uses, raising `ValueError` naming the choices,
    and the `.pyi` annotates it `str`. A choice on the count of a count-in,
    array-out method, which sizes the result, is refused naming it.

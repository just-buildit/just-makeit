- **A capsule param given a wrong object raises `TypeError`, not
    `AttributeError`** (gh-2355). A capsule-typed method param unwraps by duck
    typing (the capsule itself, or an object exposing `_capsule`), and an
    object that was neither leaked the lookup's error,
    `'int' object has no attribute '_capsule'`, which names an implementation
    detail instead of the requirement. It is now a `TypeError` naming the param
    and the capsule it wants, as a constructor param already said (gh-790), and
    a closed handle's own `RuntimeError` still propagates. **Behaviour
    change:** a caller that caught `AttributeError` from a capsule method
    param must catch `TypeError`. The emitter's `explain_type_error` switch,
    which kept a method on the raw error, had no False left and is removed.

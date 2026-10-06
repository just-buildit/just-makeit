- **`_inherited_error` says what it does** (gh-1965). Its docstring
    still said a teardown inherits `error` and `error_message` from the
    `exit` finalizer "both keys or neither", the rule gh-864 retired. The
    code inherits each key on its own, as `docs/declarative-scaffolding.md`
    says. The docstring now states that rule and its reason, once, and the
    comment that contradicted it is gone. No behaviour change.

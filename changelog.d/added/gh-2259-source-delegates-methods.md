- **A composer's generating source can expose its generator's methods**
    (gh-2259). `[module.X.source.generates] methods = ["prod"]` binds each
    named method of the generator object on the source type, so
    `Synth(...).prod(x)` works without building the generator by hand. The
    method runs against the generator `steps()` uses. The first call builds
    it through `bridge_fn` and refuses as `steps()` does. Later calls, from
    either method, continue the same state. The binding, its runtime
    docstring and its `.pyi` member are the generator's, written by the same
    emitter. Their `Raises` also lists what that first build can raise, as
    `steps()`'s does, and an error the binding raises names the source
    (`Synth.prod: ...`). Only the listed names are delegated. A name is
    refused when the manifest loads if the generator does not declare it or
    the source already has that member (a field, `fs`, a computed property,
    `steps`, `step`, `reset`). It is also refused if the source cannot carry
    the method: a built-in member of the generator, a `varargs` or
    `manual_stub` method, a borrow or its release, a `single` record, or a
    `codec` method.

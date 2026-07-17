# Kitchen sink — the integration surface, one project

This example builds a single jm project that combines the features most likely
to break *each other*: a vendored external C library, cross-component
`depends_on`, GIL release (`nogil`), component-level `extra_link_libs`, a
hand-written `no_generate` sibling module, an app face, and every object
flavor.

Integration bugs hide in the *combinations* — a feature that works alone breaks
when used with another. (Building this example is what surfaced jm gh-174: a
`depends_on` object whose C test failed to link its dependency.) Running it in
CI guards that surface on every push.

It is deliberately not exhaustive: it covers the features that touch the build
and link graph. Composites, streaming, and declarative diagnostics have their
own examples in the gallery.

Run it end to end:

```sh
jm example kitchen_sink
```

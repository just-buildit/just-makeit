# Kitchen sink — every feature, one project

This example builds a single jm project that exercises **every** major feature
at once: a vendored external C library, cross-component `depends_on`, GIL
release (`nogil`), component-level `extra_link_libs`, and every object flavor.

Integration bugs hide in the *combinations* — a feature that works alone breaks
when used with another. (Building this example is what surfaced jm gh-174: a
`depends_on` object whose C test failed to link its dependency.) Running it in
CI guards that surface on every push.

Run it end to end:

```sh
jm example kitchen_sink
```

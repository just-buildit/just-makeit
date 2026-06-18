## The generated class

After `jm apply` and a cmake build, `composites.ring` exposes a fully typed
`Ring` class — entirely from the manifest, no hand-written Python or binding:

```{use.py}
```

That is the whole point of the handle generator: a real, opaque C resource
wears an ergonomic typed-class face — constructor, array methods, decoded
properties, a writable property, and RAII — with the only hand code being the
resource's C. The capsule and composer generators cover the other two shapes of
the same idea (free functions over a handle, and an object built of objects);
see [`docs/object-of-objects.md`](../object-of-objects.md) for all three.

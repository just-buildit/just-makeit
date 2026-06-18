# Composites — the `kind = "handle"` generator

This example builds one jm project that demonstrates the **object-of-objects
`kind = "handle"` generator**: a single typed CPython class generated over an
opaque hand-C resource handle, with RAII lifetime.

A handle module is the *resource* shape — a file writer, a socket, a session,
a clock. You hand-write only the resource's C; `jm apply` materializes the
whole binding: the typed class, its constructor, its methods, decoded-from-a-
getter properties, and the context-manager / `close()` protocol.

It is the focused, teaching member of the capsule / composer / handle family.
For the full surface (capsule free-functions, composer object-of-objects, and
every handle shape) see [`docs/object-of-objects.md`](../object-of-objects.md).

Run it end to end:

```sh
jm example composites
```

## 1. Scaffold an allocator

```{01_scaffold.sh}
```

`capacity` and `slots` are `--init-param`s: they are what the caller passes,
and `create()` consumes them without storing them. The three `--state` fields
are what the object *derives* and keeps — declaring init-params is what keeps
them out of the constructor signature, so `Allocator(capacity=9, slots=3)` is
the whole public API.

`degraded` is the one to watch. It is an ordinary `bool` on the state struct
that `create()` sets, and in step 3 it becomes the entire mechanism behind a
Python warning.

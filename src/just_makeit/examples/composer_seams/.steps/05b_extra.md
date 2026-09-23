## 5b. A method jm cannot express

The seams cover what a source *is*. Now and then a type needs one method jm
has no vocabulary for. `extra_methods` is the escape hatch: the manifest row
in step 3 declares the name, the flags and the Python signature, and the body
is plain CPython in a file jm never touches.

`native/src/playlist/playlist_ext_extra.c`:

```{05b_playlist_ext_extra.c}
```

jm `#include`s the file after the four generated types, so the body can use
anything they define. It also forward-declares `Mix_total_samples` above the
method table that names it, with the signature `METH_NOARGS` implies: write
that exact signature, or the file does not compile.

The order does not matter either. The row alone makes the binding include the
file, so it can be written before or after the `apply` that declared it. If it
is missing, the build fails naming `playlist_ext_extra.c`.

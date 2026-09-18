## 5. Write the two seams

`native/src/backing/playlist_bridge.c`:

```{05_playlist_bridge.c}
```

Two things are worth noticing.

The file **includes the generated header** rather than declaring anything.
That is what makes the split safe: if the manifest renames `bridge_fn`, or a
computed property changes type, this file stops compiling instead of quietly
linking against a signature that no longer matches.

`clip_why_not` is the optional third seam (gh-1307). A bridge returns NULL
for configuration reasons far more often than for memory, and a bare NULL
reached Python as `RuntimeError: clip_from_source returned NULL` whatever the
reason. The error function is asked only after a refusal, with the same
arguments, and its sentence is raised as `ValueError` — the category a
refused `create()` gets. Returning NULL from it keeps the `RuntimeError`, so
a genuine allocation failure still reads as one.

And there is no CPython in it. The seams exist precisely so that the parts
only you can write stay in plain C — the marshalling, the type objects, the
reference counting and the error translation are all on jm's side of the line.

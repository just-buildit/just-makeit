## 4. Apply

```{04_apply.sh}
```

```
  create  native/inc/playlist/playlist_bridge.h
  create  native/src/playlist/CMakeLists.txt
  create  native/src/playlist/playlist_ext.c
  create  src/studio/playlist/playlist.pyi
  update  CMakeLists.txt
```

The first of those is the gh-998 file, and it is the only header a composer
module emits:

```c
/* Build the composed generator from a source config (source -> generator). */
clip_state_t *clip_from_source(const clip_t *, double);

/* Computed read-only property `duration`. */
double clip_duration(const clip_t *);
```

Self-contained on purpose — it pulls in the backing header and the
generator's, so a consumer does not have to work out what to include first. It
is emitted **only** when the source declares at least one seam; a composer
with neither gets no header at all, because there would be nothing to say.

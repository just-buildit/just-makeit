## 2. Write the backing kernel

A composer does not generate its kernel — `backing = "playlist"` names C that
is yours. It lives in a `c_deps` directory, which is jm's escape hatch for
hand-written C: jm emits an `add_subdirectory()` line for it and never touches
anything inside.

`native/inc/playlist/playlist_core.h`:

```{02_playlist_core.h}
```

`native/src/backing/playlist_core.c`:

```{02_playlist_core.c}
```

`native/src/backing/CMakeLists.txt`:

```{02_CMakeLists.txt}
```

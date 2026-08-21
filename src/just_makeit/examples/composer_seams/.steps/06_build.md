## 6. Build

```{06_build.sh}
```

The composer's `.so` links `clip_core` (the generator) and `backing_core`
(the `c_deps` library) alongside NumPy. CMake will not pull an OBJECT
library's objects through another target transitively, which is why both are
named in the manifest rather than left to propagate.

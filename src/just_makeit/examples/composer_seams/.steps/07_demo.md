## 7. Drive it from Python

```{07_demo.py}
```

```
Clip(gain=2.0).gain      -> 2.0
           .duration     -> 4.0   (clip_duration, in C)
  after gain = 5.0       -> 10.0  (recomputed, not stored)
           .duration = 1.0 -> AttributeError (read-only)
Clip(gain=7.0).steps(3)  -> [7.+0.j 7.+0.j 7.+0.j]   (via clip_from_source)
Mix(Track.sum(2,3,dur=4)).execute(8) -> [5.+0.j 5.+0.j 5.+0.j 5.+0.j]
mix.segments             -> 1 track(s), repeat=False, continuous=False
composer_seams demo: PASSED
```

`duration` is the argument for computing rather than storing: reassigning
`gain` changes it, and there is no cache to invalidate because there is no
cache. And `Clip.steps()` is a generator the source type never contained —
`clip_from_source` builds it on demand, which is why a *config* object can
produce samples at all.

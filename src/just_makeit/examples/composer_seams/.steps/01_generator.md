## 1. Scaffold the generator being composed

```{01_generator.sh}
```

Nothing composer-specific yet — this is a plain `jm object`. It matters
because the composer's defaults are named after it: declaring
`generator = "clip"` makes jm expect `clip_state_t`, `clip_step`,
`clip_steps`, `clip_reset`, `clip_destroy` and `clip/clip_core.h`, all of
which `jm object` has just produced. Every one is overridable in the manifest;
none of them needs to be here.

`--arg-type void --return-type "float _Complex"` is what makes it a *source*:
`clip_step(state)` takes no input and returns a sample, and
`clip_steps(state, out, n)` fills a block.

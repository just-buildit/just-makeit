## 1. Scaffold the generator being composed

```{01_generator.sh}
```

Nothing composer-specific yet — this is a plain `jm object`. It matters
because the composer's defaults are named after it: declaring
`generator = "clip"` makes jm expect `studio_clip_state_t`, `studio_clip_step`,
`studio_clip_steps`, `studio_clip_reset`, `studio_clip_destroy` and `studio/clip/clip_core.h`, all of
which `jm object` has just produced. Every one is overridable in the manifest;
none of them needs to be here.

`--arg-type void --return-type "float _Complex"` is what makes it a *source*:
`studio_clip_step(state)` takes no input and returns a sample, and
`studio_clip_steps(state, out, n)` fills a block.

Give the generator a body. A constant-level source keeps the composed output
checkable by eye: in `native/inc/studio/clip/clip_core.h`, replace the two
lines of the `studio_clip_step` stub's body so that it reads:

```c
static inline float _Complex
studio_clip_step(const studio_clip_state_t *state)
{
    return (float _Complex)state->level;
}
```

## 4. Implement

### FIR filter

Open `native/inc/fir/fir_core.h` and replace `fir_step`.  The delay line
is mutated, so the signature drops `const`:

```c
static inline float complex
fir_step(fir_state_t *state, float complex x)
{
    memmove(&state->delay[1], &state->delay[0],
            (16 - 1) * sizeof(float complex));
    state->delay[0] = x;

    float complex y = 0.0f;
    for (int k = 0; k < 16; k++)
        y += state->coeffs[k] * state->delay[k];
    return (float complex)state->gain * y;
}
```

### Biquad filter (Direct Form II transposed, real)

Open `native/inc/biquad/biquad_core.h` and replace `biquad_step`.
Delay states `w1`/`w2` are written each call, so `const` drops here too:

```c
static inline float
biquad_step(biquad_state_t *state, float x)
{
    double y   = state->b0 * (double)x + state->w1;
    state->w1  = state->b1 * (double)x - state->a1 * y + state->w2;
    state->w2  = state->b2 * (double)x - state->a2 * y;
    return (float)y;
}
```

`double` arithmetic avoids coefficient-quantisation noise accumulation in the
delay states; the output is narrowed back to `float` on return.

> **Note:** both `fir_steps()` and `biquad_steps()` in their respective
> `_core.c` files loop over `_step()` automatically — no changes needed there.

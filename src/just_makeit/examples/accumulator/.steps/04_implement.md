## 4. Implement

All implementations are trivially short. jm generates all the scaffolding;
you only fill in the algorithm body.

### step — one line, same pattern for both types

Open the generated header and replace the `(void)state; (void)x; /* TODO: implement */`
stub. The only difference between the two is the C type — the logic is
`state->acc += x` in both cases:

**`native/inc/acc_f32/acc_f32_core.h`**

```c
static inline void
acc_f32_step(acc_f32_state_t *state, float x)
{
    state->acc += x;
}
```

**`native/inc/acc_cf64/acc_cf64_core.h`**

```c
static inline void
acc_cf64_step(acc_cf64_state_t *state, double complex x)
{
    state->acc += x;
}
```

`steps()` is already done — jm generates the batch loop in `_core.c` that
calls `step()` for each element. You get `steps()` = `add()` for free.

### Named methods — `native/src/acc_f32/acc_f32_core.c`

`get` reads the accumulator. `dump` is the interesting one: capture first,
then zero, then return the captured value. The order matters.

```c
float
acc_f32_get(acc_f32_state_t *state)
{
    return state->acc;
}

float
acc_f32_dump(acc_f32_state_t *state)
{
    float v = state->acc;
    state->acc = 0.0f;
    return v;
}

void
acc_f32_madd(
    acc_f32_state_t *state,
    const float *x, size_t x_len,
    const float *h, size_t h_len)
{
    size_t n = x_len < h_len ? x_len : h_len;
    for (size_t i = 0; i < n; i++)
        state->acc += x[i] * h[i];
}

void
acc_f32_add2d(acc_f32_state_t *state, const float *x, size_t x_len)
{
    for (size_t i = 0; i < x_len; i++)
        state->acc += x[i];
}

void
acc_f32_madd2d(
    acc_f32_state_t *state,
    const float *x, size_t x_len,
    const float *h, size_t h_len)
{
    size_t n = x_len < h_len ? x_len : h_len;
    for (size_t i = 0; i < n; i++)
        state->acc += x[i] * h[i];
}
```

### Named methods — `native/src/acc_cf64/acc_cf64_core.c`

Note the `(double)h[i]` cast in `madd` and `madd2d`: `h` is `float` (real
weights), `x` is `double complex`. Widening before the multiply preserves
precision in the intermediate result.

```c
double complex
acc_cf64_get(acc_cf64_state_t *state)
{
    return state->acc;
}

double complex
acc_cf64_dump(acc_cf64_state_t *state)
{
    double complex v = state->acc;
    state->acc = 0.0 + 0.0 * I;
    return v;
}

void
acc_cf64_madd(
    acc_cf64_state_t *state,
    const double complex *x, size_t x_len,
    const float *h, size_t h_len)
{
    size_t n = x_len < h_len ? x_len : h_len;
    for (size_t i = 0; i < n; i++)
        state->acc += x[i] * (double)h[i];
}

void
acc_cf64_add2d(
    acc_cf64_state_t *state,
    const double complex *x, size_t x_len)
{
    for (size_t i = 0; i < x_len; i++)
        state->acc += x[i];
}

void
acc_cf64_madd2d(
    acc_cf64_state_t *state,
    const double complex *x, size_t x_len,
    const float *h, size_t h_len)
{
    size_t n = x_len < h_len ? x_len : h_len;
    for (size_t i = 0; i < n; i++)
        state->acc += x[i] * (double)h[i];
}
```

The patch scripts automate these edits:

```sh
python3 .steps/04_patch_f32.py
python3 .steps/04_patch_cf64.py
```

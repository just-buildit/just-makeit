# Stateful vs Pure Objects

| Shape              | Flags                       | `step()` signature                   |
| ------------------ | --------------------------- | ------------------------------------ |
| Stateful (default) | _(none)_                    | `step(const state_t *s, T x) → T`    |
| Mutating           | `--mutable`                 | `step(state_t *s, T x) → T`          |
| Generator          | `--arg-type void`           | `step(state_t *s) → T`               |
| Mutating generator | `--arg-type void --mutable` | `step(state_t *s) → T`               |
| Sink               | `--return-type void`        | `step(const state_t *s, T x) → void` |
| Pure function      | `--no-state`                | `step(const state_t *s, T x) → T`    |
| Method-only        | `--no-step`                 | _(no step generated)_                |

The sections below explain each shape and when to reach for it. Each flag
also works at scaffold time, e.g. `jm new my_proj --object framer --no-step`
or `--no-state` in a single command.

______________________________________________________________________

## Default: stateful, const step

```sh
just-makeit object engine --state gain:double:1.0
```

The default shape: a C struct holds state between calls; `step()` takes a
`const` pointer to that struct and a single input sample.

```c
typedef struct { double gain; } engine_state_t;

static inline float complex
engine_step(const engine_state_t *state, float complex x)
{
    /* <<IMPLEMENT>> */
    return x;
}
```

`const` on the state pointer is intentional — it signals that `step()` reads
state but does not change it. The compiler can exploit this for optimisation,
and it prevents accidental mutation in the hot path.

Use this shape for anything whose algorithm is a pure function of the current
input and stored parameters: filters, mixers, scalers, correlators.

______________________________________________________________________

## Mutating generator: `--mutable`

```sh
just-makeit object nco --arg-type void --return-type "float _Complex" --mutable \
    --state phase:double:0.0 --state freq_hz:double:1000.0
```

Remove `const` from the state pointer so `step()` can write back to the struct.
Required for objects that advance internal state on every call — an NCO that
increments its phase, a counter, a PRNG.

```c
static inline float complex
nco_step(nco_state_t *state)   /* no const */
{
    /* advance phase, return sample */
}
```

`--arg-type void` (see below) and `--mutable` are independent flags; combine
them whenever the generator needs to mutate state.

______________________________________________________________________

## Generator / source: `--arg-type void`

```sh
just-makeit object tone_gen --arg-type void --return-type "float _Complex" --mutable
```

A generator produces samples without consuming input. Pass `--arg-type void`
to suppress the input parameter from `step()` and `steps()`. With no explicit
`--return-type`, a void-arg object defaults to a `float _Complex` return
(`--return-type void` is rejected as a no-op object).

```c
/* no input parameter */
static inline float complex
tone_gen_step(tone_gen_state_t *state) { … }

void tone_gen_steps(tone_gen_state_t *state, float complex *output, size_t n);
```

Python side:

```python
gen = ToneGen(freq_hz=1000.0)
sample = gen.step()                             # no argument
block  = gen.steps(1024)                        # only length, no input array
```

______________________________________________________________________

## Sink: `--return-type void`

```sh
just-makeit object iq_writer --return-type void \
    --state filename:const_char_p
```

A sink consumes input but produces no output. Pass `--return-type void` to
drop the return value from `step()` and `steps()`.

```c
static inline void
iq_writer_step(const iq_writer_state_t *state, float complex x) { … }
```

Python side:

```python
sink = IqWriter()
sink.step(sample)           # returns None
sink.steps(block)           # returns None
```

______________________________________________________________________

## Pure function: `--no-state`

```sh
just-makeit object clipper --no-state \
    --init-param threshold:float:1.0
```

Some operations require no persistent state — they are pure functions of their
inputs. `--no-state` suppresses the state struct entirely; `--init-param`
declares constructor parameters that are passed in at creation time but not
stored as struct fields (you implement storage yourself in the `<<IMPLEMENT>>`
stubs).

```c
/* No state struct generated.  You define storage in the create stub. */
clipper_state_t *clipper_create(float threshold);
void             clipper_destroy(clipper_state_t *state);
void             clipper_reset(clipper_state_t *state);

static inline float complex
clipper_step(const clipper_state_t *state, float complex x) { … }
```

Use `--no-state` when the constructor signature cannot be expressed as a flat
list of typed fields — for example, a filter initialised from a `const float *taps, size_t num_taps` pair.

______________________________________________________________________

## Method-only object: `--no-step`

```sh
just-makeit object framer --no-step \
    --state buf_len:size_t:0
just-makeit method framer push --param data:"float _Complex[]" --return-type size_t
just-makeit method framer flush --return-type "float _Complex[]" --variable-output
```

When the object's interface consists entirely of named methods, suppress
`step()` and `steps()` with `--no-step`. The lifecycle functions (`create`,
`destroy`, `reset`) are still generated; only the generic execute pair is
omitted.

Use this shape for stateful objects with non-uniform interfaces: re-framers,
packetisers, protocol encoders.

See [Scaffold commands](commands/scaffold.md) for the full flag reference, and
[Examples](examples/index.md) for complete walkthroughs of each shape.

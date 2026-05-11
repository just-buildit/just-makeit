# Scaffold feedback

## Commands run

```sh
uvx just-makeit new doppler --module source

uvx just-makeit object nco --module source \
    --state norm_freq:float:0.0 \
    --state nmax:uint32_t \
    --return-type float

uvx just-makeit method nco step_radians --module source --return-type float
uvx just-makeit method nco step_radians_ovf --module source --return-type float --multi-output uint8_t

uvx just-makeit method nco step_norm --module source --return-type float
uvx just-makeit method nco step_norm_ovf --module source --return-type float --multi-output uint8_t

uvx just-makeit method nco step_u32 --module source --return-type uint32_t
uvx just-makeit method nco step_u32_ovf --module source --return-type uint32_t --multi-output uint8_t

uvx just-makeit property nco norm_freq --module source --type float --writable
uvx just-makeit property nco phase --module source --type uint32_t --writable
uvx just-makeit property nco phase_inc --module source --type uint32_t

uvx just-makeit object lo --module source \
    --state norm_freq:float:0.0 \
    --return-type "float _Complex"

uvx just-makeit method lo step_ovf --module source \
    --return-type "float _Complex" --multi-output uint8_t

uvx just-makeit property lo norm_freq --module source --type float --writable
uvx just-makeit property lo phase --module source --type uint32_t --writable
uvx just-makeit property lo phase_inc --module source --type uint32_t
```

## Questions / issues found

### 1. `--arg-type void` not supported

Wanted to express "no input" for a source/generator object.
Used `--return-type` alone (default arg-type becomes `float _Complex`),
which generates a `nco_step(state, float complex x)` signature and a
`nco_steps(state, input, output, n)` that treats NCO as a filter rather
than a generator.

**Ask:** support `--arg-type void` (or a `--source` / `--generator` flag)
to emit a no-input step signature and a `nco_steps(state, output, n)` block path.

---

### 2. `--multi-output` not wired into `source_ext.c`

`_ovf` methods in `source_ext.c` return only the primary value — no tuple.
The C stub in `nco_methods.c` also only returns the primary type with no
out-parameter for the overflow flag.

Expected:
```c
/* C stub */
float nco_step_radians_ovf(nco_state_t *state, uint8_t *ovf);

/* Python wrapper */
uint8_t ovf = 0;
float y = nco_step_radians_ovf(self->handle, &ovf);
return Py_BuildValue("(fB)", y, ovf);
```

**Ask:** `--multi-output T` should add an `T *out_N` parameter to the C stub
and emit the `Py_BuildValue` tuple in the Python wrapper.

---

### 3. Properties scaffolded as getters/setters don't add fields to the struct

`just-makeit property nco phase --type uint32_t --writable` wires up
`nco_get_phase` / `nco_set_phase` in `source_ext.c` but does not add
`phase` or `phase_inc` to `nco_state_t`. The getter/setter stubs are left
with `<<IMPLEMENT>>` comments.

This is fine for computed properties, but for stored state it requires a
manual struct edit. Is there a way to express "this property is backed by
a struct field" (i.e. `--state` but exposed as a property instead of a
constructor arg)?

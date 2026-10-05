## 2. Implement step()

Replace the generated stub in `native/inc/my_power/power_est/power_est_core.h` with
the recursive O(1) update.  The delay line stores `|x|²` for each past sample;
`sum_sq` is the running total.

```{02_step_impl.c}
```

Apply it with the patch script that ships with just-makeit, in this
example's `.steps/` directory. Run it from the project root by that path
(the later sections reuse `STEPS`):

```sh
STEPS="$(python3 -c 'import just_makeit, pathlib; print(pathlib.Path(just_makeit.__file__).parent / "examples/sliding_power/.steps")')"
python3 "$STEPS/02_patch.py"
```

While the header is open, give the Python class a real docstring: replace
the scaffold's `@brief Create a power_est instance.` above
`my_power_power_est_create()` with your own sentence (for example
`@brief Create a sliding-window signal-power estimator over a 64-sample
window, zeroed.`), then re-derive the `.pyi` from it. `apply` leaves your
`step()` body alone:

```sh
just-makeit apply
```

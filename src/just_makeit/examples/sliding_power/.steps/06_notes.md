## Numerical notes

**The three approaches compute the same quantity** — they differ only in
how they maintain the running sum.

| Approach | Cost per sample | Drift |
|---|---|---|
| Standard MA (full recompute) | O(N) adds | none — always exact |
| Recursive O(1), float32 acc | O(1) | grows as √n |
| Recursive O(1), double acc | O(1) | negligible |
| Recursive + periodic recompute | O(1) amortised | bounded |

**Why drift happens in the recursive form:**
Each `step()` does `sum_sq += new² − old²`.  On paper this is exact, but in
floating-point the subtraction can cancel significant bits, and the residual
error accumulates.  Over N samples the error grows roughly as
`sqrt(N) * eps * sum_sq`.

**Why it barely matters with a double accumulator:**
The delay line holds `float` values (4-byte, ~7 decimal digits).  The
accumulator is `double` (8-byte, ~15 digits).  The inputs only carry 7
digits of information, so the accumulator's extra precision absorbs all
rounding residuals.  Over 5 million noise samples the error stays at
~10⁻¹⁵ — below the float32 quantisation noise floor of ~10⁻⁷.

**When the SIMD recompute earns its keep:**
If the accumulator were `float` (e.g., for an embedded target with no FPU
double path), drift grows to ~10⁻⁵ by 5M samples and keeps climbing.  A
full recompute every 1000 samples — the one call that fits a single SIMD
vector of 16 float32 lanes — resets the error to zero.

You can reproduce these numbers yourself:

```sh
python3 .steps/06_compare.py
```

Output (5M noise samples):

```
Demo 2: float32 accumulator
    sample      no-cal  calibrated      std MA   err(no-cal)    err(cal)
   500,000      0.9821      0.9821      0.9821      1.66e-06    0.00e+00
 5,000,000      1.2276      1.2277      1.2277      6.89e-05    0.00e+00

Demo 3: double accumulator (what the C code uses)
    sample   recursive      std MA           err
   500,000      0.9821      0.9821      8.88e-16
 5,000,000      1.2277      1.2277      8.88e-16
```

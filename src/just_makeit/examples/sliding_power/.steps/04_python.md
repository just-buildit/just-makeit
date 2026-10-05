## 4. Python demo

`make` built the extension into `src/my_power/`; an editable install points
Python there (it compiles nothing). Then run the demo below:

```sh
pip install -e .
python3 "$STEPS/04_demo.py"
```

```{04_demo.py}
```

Expected output:

```
sine   power (expect ~0.500): 0.5000
noise  power (expect ~1.000): 0.9135
silence power (expect 0.000): -0.0000
steps() final power (expect ~0.500): 0.5000
```

The silence line reads `-0.0000`: subtracting the samples back out of the
running sum leaves a rounding residue just below zero. Section 5 and the
numerical notes cover that drift.

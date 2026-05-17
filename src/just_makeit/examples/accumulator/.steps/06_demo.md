## 6. Use from Python

```{06_demo.py}
```

Run it from `my_acc/`:

```sh
python3 .steps/06_demo.py
```

Expected output:

```
AccF32 after push 1+2+3: get() = 6.0
AccF32 after steps(ones*100): get() = 100.0
AccF32 dump() = 42.0, get() after = 0.0
AccF32 madd([1,2,3,4], [0.25]*4): get() = 2.5
AccF32 add2d(3x4 arange): get() = 66.0
AccCf64 after push (1+2j)+(3+4j): get() = (4+6j)
AccCf64 madd: get() = (2.75+2.75j)
AccCf64 dump() = (5+6j), get() after = 0
```

All operations go through the C extension with no Python arithmetic.  The
`steps()` method is the auto-generated batch loop — you get it for free without
writing a single line of looping code.

## 7. Round-trip demo

```{07_demo.py}
```

The demo generates 4096 complex samples, writes them to a temporary `.q15`
file, reads them back, and verifies the round-trip error stays within one
quantisation step (~1/32767 ≈ −90 dBFS):

```
wrote    4096 complex samples -> /tmp/tmpXXXXXX.q15  (16384 bytes)
written: 4096 samples
read:    4096 samples,  eof=1
max err: 0.000031  (floor ~0.000031)
PASSED
```

Note the file size: 4096 samples × 4 bytes (two `int16_t`) = 16 384 bytes —
half the 32 768 bytes a cf32 file would use for the same signal.

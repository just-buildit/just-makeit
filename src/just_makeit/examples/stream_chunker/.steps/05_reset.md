## 5. reset()

`reset()` sets `n_buf = 0` and zeroes `buf` — any partially accumulated
samples are discarded.  Useful at stream boundaries or after error recovery.

```python
c.reset()
# Next push() starts with an empty accumulation buffer
```

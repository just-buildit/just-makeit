## 8. Use from Python

```{08_demo.py}
```

`Gain` and `Ema` are independent stateful objects — chain them however you need.
`steps()` is also available for block processing:

```python
y = Gain(gain=2.0).steps(signal)   # returns float32 ndarray
```

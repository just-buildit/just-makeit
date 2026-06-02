## 1. Scaffold

```{01_scaffold.sh}
```

One state variable — `gain` — gives the Python constructor a keyword argument
(`Filter(gain=2.0)`) and a C-side field that `step()` and `configure()` both
share.

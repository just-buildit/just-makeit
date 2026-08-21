## 1. Scaffold a collector

```{01_scaffold.sh}
```

`step()` takes a `double` and returns nothing — it records. The two fixed
length array state fields are the ring the records live in, and `count` is how
many have ever arrived. Everything the three methods report is read back out
of that ring.

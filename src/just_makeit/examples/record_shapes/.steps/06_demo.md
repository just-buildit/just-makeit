## 6. Read the same ring back three ways

```{06_demo.py}
```

```
summary()  -> Summary(n=4, mean=1.25)
             type=Summary  n=4  mean=1.2500
read()     -> array([(0, 0.5 ), (1, 2.5 ), (2, 0.25), (3, 1.75)],
                    dtype=[('t', '<u8'), ('v', '<f8')])
             dtype=[('t', '<u8'), ('v', '<f8')]
peaks()    -> [(1, 2.5), (3, 1.75)]
record_shapes demo: PASSED
```

The `read()` dtype is the part worth pausing on. `('t', '<u8'), ('v', '<f8')`
was not written down anywhere — jm has never seen inside `evlog_rec_t`. The
generated binding builds that dtype the first time `read()` is called, from
`offsetof(evlog_rec_t, t)` and `sizeof`, so it stays correct if you reorder
the struct or the compiler pads it differently.

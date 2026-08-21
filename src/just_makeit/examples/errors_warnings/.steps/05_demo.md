## 5. All four, from Python

```{05_demo.py}
```

```
1. refuse     -> ValueError: capacity must cover at least one unit per slot
2. caveat     -> RuntimeWarning: capacity is not divisible by slots; the remainder is unusable
   (exact fit) -> 0 warnings
3. take(5)    -> None   (remaining now 4)
   take(100)  -> ValueError: requested more than remains (rc=1)
4. peek(0)    -> 1
   peek(99)   -> IndexError: no such slot (rc=-1)
errors_warnings demo: PASSED
```

Two details worth keeping:

- **`take()` returns `None` on success, not `0`.** `--status-return` means the
    `int` carried nothing but status, so there is no result to hand back. Its
    neighbour `peek()` returns a real number from an identically-shaped C
    function, and the only reason they differ is the flag.
- **The failing code reaches the message** — `(rc=1)`, `(rc=-1)`. Your
    `--error-message` says what went wrong in prose; jm appends what the kernel
    actually returned, which is the part you need when several codes share one
    category.

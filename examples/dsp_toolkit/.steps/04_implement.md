## 4. Implement ema

`ema_step` must write back to `state->prev`, so the signature drops `const`:

```{04_step_after.c}
```

The patch script handles both the body replacement and the `const` removal:

```{04_patch.py}
```

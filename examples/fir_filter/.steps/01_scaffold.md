## 1. Scaffold

```{01_scaffold.sh}
```

Three state variables:

| Name     | Type                  | Role                         | Constructor param?           |
|----------|-----------------------|------------------------------|------------------------------|
| `coeffs` | `float[16]`           | Real tap weights             | No — load via `set_coeffs()` |
| `delay`  | `float _Complex[16]`  | Complex delay line (history) | No — zero on create/reset    |
| `gain`   | `float`               | Output scalar gain           | Yes — default `1.0`          |

`coeffs` and `delay` are inline in the C struct — no heap allocation per field.

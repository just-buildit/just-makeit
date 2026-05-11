## 1. Scaffold

```{01_scaffold.sh}
```

Scaffolds a single `gain` component with a real-valued `step()`:

| Name   | Type    | Default | Role        |
| ------ | ------- | ------- | ----------- |
| `gain` | `float` | `1.0`   | Scalar gain |

`make` configures CMake and builds the extension. The C test already passes
before you write a single line of logic.

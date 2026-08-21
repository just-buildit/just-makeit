## 3. Implement — the C only ever sets flags and returns codes

```{03_patch.py}
```

Nothing in this file mentions Python. `create()` returns `NULL` or sets a
`bool`; the two methods return an `int`. Every exception and warning the next
step shows is jm's glue reading those, which is why none of this needed a
`#include <Python.h>` and why the declarations in step 2 touched no sacred
file.

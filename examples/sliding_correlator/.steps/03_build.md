## 3. Build and verify

```{03_build.sh}
```

Quick sanity check — identity correlator (ref = [1, 0, …, 0]) passes the
input through unchanged:

```python
import numpy as np
from my_corr import SlidingCorrelator

c = SlidingCorrelator()
ref = np.zeros(16, dtype=np.complex64)
ref[0] = 1.0
c.set_ref(ref)

impulse = np.zeros(16, dtype=np.complex64)
impulse[0] = 1.0
print(c.steps(impulse)[:4].tolist())
# [(1+0j), 0j, 0j, 0j]
```

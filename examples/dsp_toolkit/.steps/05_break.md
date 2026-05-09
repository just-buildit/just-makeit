## 5. Both components are exported automatically

After `just-makeit init`, `__init__.py` is updated in-place — the new import
and `__all__` entry are spliced in without touching anything else:

```python
"""dsp_toolkit — Gain component."""

from .gain import Gain
from .ema import Ema

__all__ = ["Gain", "Ema"]
```

Existing imports and any user additions are preserved.

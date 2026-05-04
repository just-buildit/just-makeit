"""gain — Gain component.

Classes
-------
Gain
    Core gain processor.

Examples
--------
>>> from gain import Gain
>>> obj = Gain(1.0)
>>> result = obj.step(1.0 + 0.0j)
>>> abs(result - (1.0 + 0.0j)) < 1e-6
True
"""

from .gain import Gain

__all__ = ["Gain"]

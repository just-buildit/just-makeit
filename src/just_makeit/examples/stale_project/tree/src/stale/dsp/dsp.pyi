# dsp/dsp.pyi — type stubs for the dsp C extension.
from typing import final
import numpy as np
from numpy.typing import NDArray

@final
class Fir:
    """Fir component.

    Parameters
    ----------
    scale : float, default 1.0
        scale state variable.

    Examples
    --------
    Create with defaults:

    >>> from stale.dsp import Fir
    >>> obj = Fir(1.0)
    >>> obj.get_scale()
    1.0

    Reset restores defaults:

    >>> obj.set_scale(0.0)
    >>> obj.reset()
    >>> obj.get_scale()
    1.0

    """
    def __init__(self, scale: float = ...) -> None: ...

    def reset(self) -> None:
        """Reset state to post-create defaults."""

    def step(self, x: float) -> float:
        """Process one input sample."""

    def steps(self, x: NDArray[np.float32], out: NDArray[np.float32] | None = None) -> NDArray[np.float32]:
        """Process a samples array."""

    def decimate(self, x: NDArray[np.float32], out: NDArray[np.float32] | None = None) -> NDArray[np.float32]:
        """Decimate."""

    def decimate_max_out(self) -> int:
        """Max output length decimate() can produce for the current state."""

    def shape(self, x: float) -> NDArray[np.float32]:
        """Shape."""
    def get_scale(self) -> float:
        """Return current scale."""

    def set_scale(self, value: float) -> None:
        """Set scale."""

    @property
    def scale(self) -> float:
        """Scale."""
    @scale.setter
    def scale(self, value: float) -> None: ...

    def destroy(self) -> None:
        """Release C resources immediately."""

    def __enter__(self) -> "Fir": ...

    def __exit__(self, *args: object) -> None: ...

def energy(n: int, y: NDArray[np.float32]) -> float:
    """Energy."""

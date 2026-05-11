import numpy as np
from numpy.typing import NDArray

class Engine:
    """Engine component.

    Parameters
    ----------
    gain : np.float64, default 1.0
        gain state variable.
    """

    def __init__(self, gain: np.float64 = 1.0) -> None: ...
    def reset(self) -> None:
        """Reset state to post-create defaults."""
    def step(self, x: complex) -> complex:
        """Process one sample."""
    def steps(self, x: NDArray[np.complex64]) -> NDArray[np.complex64]:
        """Process a samples array. Returns ndarray."""
    def get_gain(self) -> np.float64:
        """Return current gain."""
    def set_gain(self, value: np.float64) -> None:
        """Set gain."""
    def destroy(self) -> None:
        """Release C resources immediately."""
    def __enter__(self) -> "Engine": ...
    def __exit__(self, *args: object) -> None: ...

import numpy as np
from numpy.typing import NDArray

class Gain:
    """Gain component.

    Parameters
    ----------
    gain : float
        Initial gain value.
    """

    def __init__(self, gain: float) -> None: ...
    def reset(self) -> None:
        """Reset state to post-create defaults."""
    def step(self, x: complex) -> complex:
        """Process one complex sample."""
    def steps(self, x: NDArray[np.complex64]) -> NDArray[np.complex64]:
        """Process a complex64 ndarray, return complex64 ndarray."""
    def get_gain(self) -> float:
        """Return current gain."""
    def set_gain(self, value: float) -> None:
        """Set gain."""
    def destroy(self) -> None:
        """Release C resources immediately."""
    def __enter__(self) -> "Gain": ...
    def __exit__(self, *args: object) -> None: ...

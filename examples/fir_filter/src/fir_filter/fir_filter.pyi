import numpy as np
from numpy.typing import NDArray

class FirFilter:
    """FirFilter component.

    Parameters
    ----------
    gain : np.float32, default 1.0
        gain state variable.
    """

    def __init__(self, gain: np.float32 = 1.0) -> None: ...
    def reset(self) -> None:
        """Reset state to post-create defaults."""
    def step(self, x: complex) -> complex:
        """Process one complex sample."""
    def steps(self, x: NDArray[np.complex64]) -> NDArray[np.complex64]:
        """Process a complex64 ndarray, return complex64 ndarray."""
    def get_gain(self) -> np.float32:
        """Return current gain."""
    def set_gain(self, value: np.float32) -> None:
        """Set gain."""
    def get_coeffs(self) -> NDArray[np.float32]:
        """Return a copy of coeffs (length 16, dtype np.float32)."""
    def get_coeffs_view(self) -> NDArray[np.float32]:
        """Return a read-only view of coeffs.

        Backed by the component's internal state buffer.
        **Do not use after destroy().**
        """
    def set_coeffs(self, value: NDArray[np.float32]) -> None:
        """Set coeffs from a np.float32 array of length 16."""
    def get_delay(self) -> NDArray[np.complex64]:
        """Return a copy of delay (length 16, dtype np.complex64)."""
    def get_delay_view(self) -> NDArray[np.complex64]:
        """Return a read-only view of delay.

        Backed by the component's internal state buffer.
        **Do not use after destroy().**
        """
    def set_delay(self, value: NDArray[np.complex64]) -> None:
        """Set delay from a np.complex64 array of length 16."""
    def destroy(self) -> None:
        """Release C resources immediately."""
    def __enter__(self) -> "FirFilter": ...
    def __exit__(self, *args: object) -> None: ...

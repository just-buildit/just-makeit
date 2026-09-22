from typing import Any, final
import numpy as np
from numpy.typing import NDArray

@final
class Gain:
    """Gain component.

    Parameters
    ----------
    level : float, default 1.0
        level state variable.

    Examples
    --------
    Create with defaults:

    >>> from stale import Gain
    >>> obj = Gain(1.0)
    >>> obj.get_level()
    1.0

    Reset restores defaults:

    >>> obj.set_level(0.0)
    >>> obj.reset()
    >>> obj.get_level()
    1.0

    """

    def __init__(self, level: float = 1.0) -> None: ...

    def reset(self) -> None:
        """Reset state to post-create defaults."""

    def step(self, x: float) -> float:
        """Process one input sample.

        Parameters
        ----------
        x : float
            Input sample.

        Returns
        -------
        float
            Output sample.
        """

    def steps(self, x: NDArray[np.float32], out: NDArray[np.float32] | None = None) -> NDArray[np.float32]:
        """Process a samples array. Returns ndarray, or fills out= if supplied."""

    def get_level(self) -> float:
        """Return current level."""

    def set_level(self, value: float) -> None:
        """Set level."""

    def destroy(self) -> None:
        """Release C resources immediately."""

    def __enter__(self) -> "Gain": ...

    def __exit__(self, *args: object) -> None: ...

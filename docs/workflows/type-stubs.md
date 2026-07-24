# Type stubs and doctests

Every object gets a `.pyi` type stub alongside its Python module. The stub
gives IDEs full type information and ships runnable doctests that pass
out-of-the-box — no setup required.

For a standalone object scaffolded with

```sh
just-makeit new my_dsp --object gain --arg-type float --return-type float \
    --state gain:float:1.0
```

the generated `src/my_dsp/gain.pyi` looks like:

```python
import numpy as np
from numpy.typing import NDArray

class Gain:
    """Gain component.

    Parameters
    ----------
    gain : float, default 1.0
        gain state variable.

    Examples
    --------
    Create with defaults:

    >>> from my_dsp import Gain
    >>> obj = Gain(1.0)
    >>> obj.get_gain()
    1.0

    Reset restores defaults:

    >>> obj.set_gain(0.0)
    >>> obj.reset()
    >>> obj.get_gain()
    1.0

    """

    def __init__(self, gain: float = ...) -> None: ...

    def reset(self) -> None:
        """Reset state to post-create defaults."""

    def step(self, x: float) -> float:
        """Process one input sample."""

    def steps(self, x: NDArray[np.float32],
              out: NDArray[np.float32] | None = None) -> NDArray[np.float32]:
        """Process a samples array. Returns ndarray, or fills out= if supplied."""

    def get_gain(self) -> float:
        """Return current gain."""

    def set_gain(self, value: float) -> None:
        """Set gain."""

    def destroy(self) -> None:
        """Release C resources immediately."""

    def __enter__(self) -> "Gain": ...

    def __exit__(self, *args: object) -> None: ...
```

## Running the doctests

The `Examples` block is a valid Python doctest. Run it after `pip install .`:

```sh
python -m doctest src/my_dsp/gain.pyi -v
```

```
Trying:
    from my_dsp import Gain
Expecting nothing
ok
Trying:
    obj = Gain(1.0)
Expecting nothing
ok
Trying:
    obj.get_gain()
Expecting:
    1.0
ok
...
3 items passed all tests:
   2 tests in gain.Gain
...
```

The doctest exercises the real C extension — construction, a getter read-back,
a setter-then-reset round-trip. For any state variable whose default value
round-trips exactly (integers and whole-number floats), the Examples section
is generated and passes automatically. Non-round-trip defaults (e.g.
`0.1f`) are omitted from doctests to avoid floating-point noise.

## What gets a stub

| Scenario                                      | Stub location                     |
| --------------------------------------------- | --------------------------------- |
| Standalone object (`just-makeit object`)      | `src/<pkg>/<obj>.pyi`             |
| Module object (`just-makeit object --module`) | `src/<pkg>/<module>/<module>.pyi` |

The stub is regenerated on every `just-makeit object`, `method`, `property`,
and `function` call. Manual edits to the generated file are overwritten —
put any extra annotations in a separate `py.typed` marker or alongside file.

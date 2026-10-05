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
from typing import final
import numpy as np
import numpy.typing as npt
from numpy.typing import NDArray

@final
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
    >>> obj = Gain(gain=1.0)
    >>> obj.get_gain()
    1.0

    Reset restores defaults:

    >>> obj.set_gain(0.0)
    >>> obj.reset()
    >>> obj.get_gain()
    1.0

    """

    def __init__(self, gain: float = 1.0) -> None: ...
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

    def steps(
        self,
        x: npt.NDArray[np.float32],
        out: npt.NDArray[np.float32] | None = None,
    ) -> NDArray[np.float32]:
        """Process a samples array. Returns ndarray, or fills out= if supplied.

        Parameters
        ----------
        x : npt.NDArray[np.float32]
            Input sample.

        Returns
        -------
        NDArray[np.float32]
            Output sample.
        """

    def get_gain(self) -> float:
        """Return current gain."""

    def set_gain(self, value: float) -> None:
        """Set gain."""

    # destroy(), __enter__() and __exit__() follow, each with a docstring
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
    obj = Gain(gain=1.0)
Expecting nothing
ok
Trying:
    obj.get_gain()
Expecting:
    1.0
ok
...
Test passed.
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

The stub is regenerated on every mutating command (`object`, `method`,
`property`, `function`, …) and on `jm apply`, so an edit to a generated
member is overwritten. To keep one, put `# jm:hand` directly above it — see
[Hand-owning one member of a generated stub](../customization.md#hand-owning-one-member-of-a-generated-stub).
`jm status` reports a hand-added member that would be lost as DROPPED.

## How an array parameter is annotated

Every array parameter, on every surface (constructor, method, module
function, `steps()`, a property setter, a handle or capsule method), is
annotated with exactly the element type the manifest declares:

| Declared                                      | Stub annotation                                             |
| --------------------------------------------- | ----------------------------------------------------------- |
| `taps:float[]`                                | `npt.NDArray[np.float32]`                                   |
| `bits:uint8_t[]` (an input)                   | `npt.NDArray[np.uint8] \| bytes \| bytearray \| memoryview` |
| `--out-param o:uint8_t[]`, `mutable`          | `npt.NDArray[np.uint8]`                                     |
| `real_type = "float[]"` on `float _Complex[]` | `npt.NDArray[np.complex64] \| npt.NDArray[np.float32]`      |
| `bank:float[][]:optional:<create_fn>`         | `npt.NDArray[np.float32] \| None`                           |

This is deliberately narrower than what the binding accepts when run: numpy
still converts a list, a tuple or an ndarray of another safely castable dtype,
so `Fir([1.0, 2.0])` works, but a type checker asks for the declared ndarray.
A one-byte integer input also names the byte buffers, because reading one
directly is jm's own behaviour rather than numpy's; a writable buffer does not,
since the binding needs a real ndarray to write into. One helper,
`_types.array_param_annotation`, spells all of them, so the two stub
generators cannot disagree.

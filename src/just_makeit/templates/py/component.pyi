import numpy as np
from numpy.typing import NDArray

class <<Component>>:
    """<<Component>> component.

    Parameters
    ----------
<<pyi_param_docs>>

<<pyi_examples>>    """

    def __init__(self, <<init_params_pyi>>) -> None: ...

    def reset(self) -> None:
        """Reset state to post-create defaults."""
<<pyi_step_methods>><<pyi_extra_methods>><<getter_setter_stubs_pyi>>
    def destroy(self) -> None:
        """Release C resources immediately."""

    def __enter__(self) -> "<<Component>>": ...

    def __exit__(self, *args: object) -> None: ...

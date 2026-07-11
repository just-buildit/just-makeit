from typing import Any<<pyi_stream_typing>>
import numpy as np
from numpy.typing import NDArray

class <<Component>>:
    """<<Component>> component.

    Parameters
    ----------
<<pyi_param_docs>>

<<pyi_examples>>    """

    def __init__(self, <<init_params_pyi>>) -> None: ...
<<builtin_reset_pyi>><<pyi_step_methods>><<pyi_extra_methods>><<pyi_stream_methods>><<getter_setter_stubs_pyi>><<property_stubs_pyi>>
    def destroy(self) -> None:
        """Release C resources immediately."""

    def __enter__(self) -> "<<Component>>": ...

    def __exit__(self, *args: object) -> None: ...

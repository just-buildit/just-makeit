"""Patch __init__.py to export both Gain and Ema.

Run from the project root: python3 .steps/06_patch_init.py
"""

import pathlib

init_py = pathlib.Path("src/dsp_toolkit/__init__.py")

init_py.write_text(
    '"""dsp_toolkit — Gain and Ema components."""\n\n'
    "from .gain import Gain\n"
    "from .ema import Ema\n\n"
    '__all__ = ["Gain", "Ema"]\n'
)
print(f"patched {init_py}")

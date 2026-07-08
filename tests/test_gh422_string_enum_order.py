"""
gh-422 — constructor codegen sorted a ``string_enum`` init_param to the
front of the positional/kwlist order regardless of its declared position,
breaking any positional construction that matches the manifest order.

Repro (doppler's ``objects/pn.toml``): declared order ``poly, seed, length,
lfsr`` (lfsr the only string_enum, declared last) generated a kwlist of
``lfsr, poly, seed, length`` — every param after the string_enum silently
shifted, so ``PN(96, 1, 7)`` bound the wrong values to the wrong params.
"""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._object import run as object_run


def _ext_c(root, obj):
    return (root / "native" / "src" / obj / f"{obj}_ext.c").read_text(
        encoding="utf-8"
    )


class TestStringEnumPreservesDeclOrder:
    def test_kwlist_matches_declared_order_enum_last(self, tmp_path):
        root = tmp_path / "wfm"
        new_run("wfm", root)
        object_run(
            root,
            "pn",
            None,
            no_state=True,
            init_params=[
                ("poly", "uint64_t", "96"),
                ("seed", "uint64_t", "1"),
                ("length", "uint32_t", "7"),
                ("lfsr", "string_enum:galois,fibonacci", "galois"),
            ],
        )
        ext = _ext_c(root, "pn")
        assert 'kwlist[] = {"poly", "seed", "length", "lfsr", NULL}' in ext

    def test_kwlist_matches_declared_order_enum_middle(self, tmp_path):
        # doppler's Detector: noise_mode (string_enum) declared 3rd of 4.
        root = tmp_path / "wfm"
        new_run("wfm", root)
        object_run(
            root,
            "detector",
            None,
            no_state=True,
            init_params=[
                ("threshold", "float", "0.5"),
                ("window", "size_t", "64"),
                ("noise_mode", "string_enum:fixed,adaptive", "fixed"),
                ("guard", "size_t", "8"),
            ],
        )
        ext = _ext_c(root, "detector")
        assert (
            'kwlist[] = {"threshold", "window", "noise_mode", "guard",'
            " NULL}" in ext
        )

    def test_pyi_init_matches_declared_order(self, tmp_path):
        root = tmp_path / "wfm"
        new_run("wfm", root)
        object_run(
            root,
            "pn",
            None,
            no_state=True,
            init_params=[
                ("poly", "uint64_t", "96"),
                ("seed", "uint64_t", "1"),
                ("length", "uint32_t", "7"),
                ("lfsr", "string_enum:galois,fibonacci", "galois"),
            ],
        )
        pyi = (root / "src" / "wfm" / "pn.pyi").read_text(encoding="utf-8")
        assert (
            "def __init__(self, poly: np.uint64 = 96,"
            " seed: np.uint64 = 1, length: np.uint32 = 7,"
            ' lfsr: str = "galois")' in pyi
        )

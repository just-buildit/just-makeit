"""gh-2380: scan the composer kind's generated binding.

Renders (`_composer.render_ext`) every composer configuration the suite
already builds a fixture for -- the module with every row kind (gh-2035:
one typed setting and one serializer parameter per scalar type), the kind-wide
module (gh-1525), an enum-backed setting (gh-1126), a capsule (gh-2346), an
owned-pointer source field (gh-1711), the bundled codegen project and the
bit-pattern source (gh-1709) -- and reads the C for any function that checks
`destroyed`, converts an argument and then hands the backing the state
without checking again (`_order_scan`).

Not scanned: `render_json_funcs` (the C `from_json` / `to_json` bodies the
CLI and the binding share), which parses JSON in C and never runs Python.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from _order_scan import unrechecked

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, SRC.as_posix())

import test_composer_codegen as codegen  # noqa: E402
import test_gh1126_composer_settings as settings  # noqa: E402
import test_gh1525_kind_arg_local_names as kindwide  # noqa: E402
import test_gh1709_coerce_str_fn as coerce  # noqa: E402
import test_gh1711_composer_owned_pointer as owned  # noqa: E402
import test_gh2035_composer_typed_rows as typed  # noqa: E402
import test_gh2346_composer_capsule as capsule  # noqa: E402
from just_makeit import _composer  # noqa: E402


def _every_row() -> "tuple[dict, str]":
    return {"project": {"name": "p"}, "module": {"mix": typed._every_row()}}, (
        "mix"
    )


def _kindwide() -> "tuple[dict, str]":
    cfg = kindwide._composer_cfg()
    mod = next(
        k for k, m in cfg["module"].items() if m.get("kind") == "composer"
    )
    return cfg, mod


def _only_module(cfg: dict) -> "tuple[dict, str]":
    return cfg, next(iter(cfg["module"]))


def _codegen() -> "tuple[dict, str]":
    return _only_module(codegen._cfg())


CONFIGS = {
    "every row kind (gh-2035)": _every_row,
    "kind-wide (gh-1525)": _kindwide,
    "enum setting (gh-1126)": lambda: (settings._cfg(), "wc"),
    "capsule (gh-2346)": lambda: (capsule._cfg(), "wc"),
    "owned pointer (gh-1711)": lambda: (owned._with(**owned.FRAME), owned.MOD),
    "bundled codegen": _codegen,
    "bit pattern (gh-1709)": lambda: _only_module(coerce._hosted()),
}

#: Functions known to have the window, by configuration. It may only shrink.
BASELINE: "dict[str, list[str]]" = {}


def _flagged() -> "dict[str, list[str]]":
    out = {}
    for label, make in CONFIGS.items():
        cfg, mod = make()
        hits = unrechecked(_composer.render_ext(cfg, mod))
        if hits:
            out[label] = [n for n, _ in hits]
    return out


@pytest.mark.parametrize("label", sorted(CONFIGS))
def test_each_configuration_renders_functions(label):
    """Armed: the scan is reading a binding, not an empty string."""
    cfg, mod = CONFIGS[label]()
    text = _composer.render_ext(cfg, mod)
    assert text.count("\nstatic ") > 20, label


def test_composer_kind_has_no_unrechecked_conversion():
    got = _flagged()
    assert got == BASELINE, (
        "a composer binding function checks `destroyed`, converts an "
        "argument (which can run Python and close the composer) and then "
        f"calls the backing with no recheck (gh-2380): {got}"
    )

"""`reexports` — a module __init__.py re-exports names from a sibling.

A module subpackage's generated ``__init__.py`` re-exports its own C-extension
types/functions. The ``[module.X] reexports`` key additionally folds names from
a sibling extension (typically a hand-written ``no_generate`` module) into the
import block and ``__all__``, so that glue regenerates cleanly from the
manifest instead of being a hand-edit that ``jm apply`` would clobber.
"""

import io
import contextlib
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._object import (  # noqa: E402
    _merge_module_init,
    _fmt_from_import,
    _fmt_all,
)
from just_makeit import _config as C  # noqa: E402


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _scaffold(dest: Path):
    _silent(new_run, "dsp", dest)
    _silent(module_run, dest, "sig")
    _silent(
        object_run,
        dest,
        "mix",
        module="sig",
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )


def _declare_reexports(dest: Path, names):
    cfg = C.load(dest)
    cfg["module"]["sig"]["reexports"] = {"fn_api": list(names)}
    C.save(dest, cfg)


NAMES = ["api_create", "api_run", "api_reset", "api_destroy", "api_get_x"]


# ── (a) reexports reach the generated __init__.py ────────────────────────────
def test_apply_folds_reexports(tmp_path):
    dest = tmp_path / "dsp"
    _scaffold(dest)
    _declare_reexports(dest, NAMES)
    _silent(apply_run, dest)
    text = (dest / "src/dsp/sig/__init__.py").read_text(encoding="utf-8")
    assert "from .sig import Mix" in text  # own export kept
    assert "from .fn_api import" in text  # reexport import emitted
    for n in NAMES:
        assert n in text
    # __all__ carries own export then reexports, in order
    assert '"Mix"' in text
    assert text.index('"Mix"') < text.index('"api_create"')


# ── (b) idempotent; single-line canonical (matches existing glue) ────────────
def test_reexports_idempotent_and_single_line(tmp_path):
    dest = tmp_path / "dsp"
    _scaffold(dest)
    _declare_reexports(dest, NAMES)
    init = dest / "src/dsp/sig/__init__.py"
    _silent(apply_run, dest)
    first = init.read_text(encoding="utf-8")
    _silent(apply_run, dest)
    assert init.read_text(encoding="utf-8") == first  # idempotent
    # Single-line canonical, like jm's other __init__.py imports — a long
    # import is NOT pre-wrapped (no churn vs the rest of the package).
    assert "from .fn_api import " + ", ".join(NAMES) in first
    assert "\n    api_create," not in first  # not parenthesised multi-line


# ── (c) user content below the glue survives ─────────────────────────────────
def test_reexports_preserve_user_content(tmp_path):
    dest = tmp_path / "dsp"
    _scaffold(dest)
    init = dest / "src/dsp/sig/__init__.py"
    init.write_text(
        init.read_text(encoding="utf-8")
        + "\n\ndef helper():\n    return 42  # user wrapper\n",
        encoding="utf-8",
    )
    _declare_reexports(dest, NAMES)
    _silent(apply_run, dest)
    text = init.read_text(encoding="utf-8")
    assert "def helper():" in text and "return 42" in text
    assert "from .fn_api import" in text


# ── (d) manifest round-trips (both writers) ──────────────────────────────────
def test_reexports_manifest_roundtrip(tmp_path):
    dest = tmp_path / "dsp"
    _scaffold(dest)
    _declare_reexports(dest, NAMES)
    cfg = C.load(dest)
    assert C.module_reexports(cfg, "sig") == {"fn_api": NAMES}
    # _dump fallback (no tomlkit) is valid TOML too
    dumped = C._dump(cfg)
    rt = tomllib.loads(dumped)
    assert rt["module"]["sig"]["reexports"] == {"fn_api": NAMES}


# ── unit tests for the merge + formatters ────────────────────────────────────
def test_merge_adds_reexports_short_single_line():
    src = 'from .sig import Mix  # noqa: E402\n__all__ = ["Mix"]\n'
    out = _merge_module_init(src, "sig", ["Mix"], {"fn_api": ["a", "b"]})
    assert "from .fn_api import a, b  # noqa: E402" in out
    assert '__all__ = ["Mix", "a", "b"]' in out


def test_fmt_from_import_is_single_line():
    line = _fmt_from_import("fn_api", NAMES + ["api_set_x", "api_get_y"])
    assert "\n" not in line  # single-line canonical, even when long
    assert line.startswith("from .fn_api import api_create, ")
    assert line.endswith("  # noqa: E402")


def test_merge_reexports_merges_existing_line():
    # an existing reexport line's names are preserved and extended
    src = (
        "from .sig import Mix  # noqa: E402\n"
        "from .fn_api import a  # noqa: E402\n"
        '__all__ = ["Mix", "a"]\n'
    )
    out = _merge_module_init(src, "sig", ["Mix"], {"fn_api": ["a", "b"]})
    assert "from .fn_api import a, b  # noqa: E402" in out
    assert out.count("from .fn_api import") == 1  # not duplicated


def test_no_reexports_is_unchanged_behaviour():
    src = 'from .sig import Mix  # noqa: E402\n__all__ = ["Mix"]\n'
    out = _merge_module_init(src, "sig", ["Mix", "Pan"])
    assert "from .sig import Mix, Pan  # noqa: E402" in out
    assert '__all__ = ["Mix", "Pan"]' in out
    assert "fn_api" not in out


def test_fmt_all_is_single_line():
    assert _fmt_all(["A", "B"]) == '__all__ = ["A", "B"]'
    long = _fmt_all([f"name_{i:02d}" for i in range(12)])
    assert "\n" not in long  # single-line canonical, even when long
    assert long.startswith('__all__ = ["name_00", ')

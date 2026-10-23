"""gh-2344: an ``[[enum]]``'s values are published to Python, once.

An enum's C int reaches Python by more than one path: an ``enum = "<name>"``
property decodes it through the binding's private ``_enum_<Class>_<name>``
table, but a struct-sequence field or a telemetry value read back from a file
carries the bare integer. Before this, a reader of that integer restated the
enum by hand. Now each object's ``.so`` adds one module attribute per enum
its tables index, built from the SAME array the binding decodes through:

* a dense enum (position is the C int) is a tuple ``NAME[v]``;
* a constant-bound enum whose ``enumerators`` are not the literal ``0..n-1``
  is a dict from the C constant to its name.

The stub declares each attribute with the matching type, and a module of
several objects publishes a shared enum once, under one name.

The built-extension tests compile the scaffold with cmake and import the
``.so`` in-process, so they need a C compiler and skip without one, as
``tests/test_preset_build.py`` does. The render tests need neither.
"""

from __future__ import annotations

import glob
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

from _compilers import default_cc
from _jmrun import run_cli

ENUM_NAME = "ENG_STATE"
VALUES = ["searching", "refining", "tracking", "idle", "lost"]

_SKIP = None
if not shutil.which("cmake"):
    _SKIP = "cmake not found"
elif default_cc() is None:
    _SKIP = "no C compiler found"


def _enum_block(*, enumerators: list[str] | None = None) -> str:
    """The ``[[enum]]`` table the manifest carries, dense unless told."""
    lines = ["[[enum]]", 'name = "eng_state"']
    lines.append("values = [" + ", ".join(f'"{v}"' for v in VALUES) + "]")
    if enumerators is not None:
        lines.append(
            "enumerators = [" + ", ".join(f'"{e}"' for e in enumerators) + "]"
        )
    return "\n".join(lines) + "\n"


def _standalone(tmp_path: Path, *, enumerators=None) -> Path:
    """A project with one standalone object whose property decodes the enum."""
    root = tmp_path / "proj"
    assert run_cli("new", "proj", cwd=tmp_path).returncode == 0
    manifest = root / "just-makeit.toml"
    manifest.write_text(
        manifest.read_text() + "\n" + _enum_block(enumerators=enumerators)
    )
    assert run_cli("object", "eng", "--state", "mode:int:0", cwd=root)
    r = run_cli(
        "property",
        "eng",
        "mode",
        "--type",
        "int",
        "--enum",
        "eng_state",
        cwd=root,
    )
    assert r.returncode == 0, r.stderr
    return root


def _module_with_two_objects(tmp_path: Path) -> Path:
    """One module, two objects that both decode the same enum."""
    root = tmp_path / "proj"
    assert run_cli("new", "proj", cwd=tmp_path).returncode == 0
    manifest = root / "just-makeit.toml"
    manifest.write_text(manifest.read_text() + "\n" + _enum_block())
    assert run_cli("module", "m", cwd=root).returncode == 0
    for obj in ("a", "b"):
        assert run_cli(
            "object", obj, "--module", "m", "--state", "mode:int:0", cwd=root
        ), obj
        r = run_cli(
            "property",
            obj,
            "mode",
            "--module",
            "m",
            "--type",
            "int",
            "--enum",
            "eng_state",
            cwd=root,
        )
        assert r.returncode == 0, r.stderr
    return root


def _build_and_import(root: Path, leaf: str):
    """Compile the scaffold with cmake and import ``<leaf>.so`` in-process."""
    import subprocess

    build = root / "build"
    # Build against THIS interpreter: the test imports the `.so` below, and a
    # `.so` for another Python version fails with a SystemError, not an
    # ImportError.
    cfg = subprocess.run(
        [
            "cmake",
            "-S",
            str(root),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert cfg.returncode == 0, f"cmake configure failed:\n{cfg.stderr}"
    bld = subprocess.run(
        ["cmake", "--build", str(build)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert bld.returncode == 0, f"build failed:\n{bld.stdout}\n{bld.stderr}"
    so = glob.glob(
        str(root / "src" / "proj" / "**" / f"{leaf}.*.so"), recursive=True
    )
    assert len(so) == 1, so
    spec = importlib.util.spec_from_file_location(leaf, so[0])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# -- the built extension -------------------------------------------------------


@pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")
def test_a_dense_enum_is_a_tuple_indexed_by_the_c_int(tmp_path):
    """The published value is the choices, in C order, as a tuple."""
    root = _standalone(tmp_path)
    mod = _build_and_import(root, "eng")
    assert getattr(mod, ENUM_NAME) == tuple(VALUES)
    # The reader's use: decode the bare integer a status field would carry.
    assert getattr(mod, ENUM_NAME)[2] == "tracking"


@pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")
def test_a_non_dense_enum_is_a_dict_from_the_c_constant(tmp_path):
    """Declared ``enumerators`` that are not 0..n-1 publish as a dict.

    The C constants are literal integers here, so the test needs no header
    of its own. Values 0, 2, 4 leave a gap a tuple could not index.
    """
    root = _standalone(tmp_path, enumerators=["0", "2", "4", "6", "8"])
    mod = _build_and_import(root, "eng")
    table = getattr(mod, ENUM_NAME)
    assert table == {
        0: "searching",
        2: "refining",
        4: "tracking",
        6: "idle",
        8: "lost",
    }


@pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")
def test_a_module_publishes_a_shared_enum_once(tmp_path):
    """Two objects index one enum; the module carries it under one name."""
    root = _module_with_two_objects(tmp_path)
    mod = _build_and_import(root, "m")
    assert getattr(mod, ENUM_NAME) == tuple(VALUES)


# -- the render, no compiler needed -------------------------------------------


def test_the_stub_declares_a_dense_enum_as_a_tuple(tmp_path):
    root = _standalone(tmp_path)
    stub = (root / "src" / "proj" / "eng.pyi").read_text()
    assert f"{ENUM_NAME}: tuple[str, ...]" in stub


def test_the_stub_declares_a_non_dense_enum_as_a_dict(tmp_path):
    root = _standalone(tmp_path, enumerators=["0", "2", "4", "6", "8"])
    stub = (root / "src" / "proj" / "eng.pyi").read_text()
    assert f"{ENUM_NAME}: dict[int, str]" in stub


def test_a_module_publishes_one_attribute_for_a_shared_enum(tmp_path):
    """One ``PyModule_AddObject`` for the name, though two objects use it."""
    root = _module_with_two_objects(tmp_path)
    ext = (root / "native" / "src" / "m" / "m_ext.c").read_text()
    if not ext:  # pragma: no cover - the path is the module's own
        pytest.fail("module aggregator not written")
    assert ext.count(f'"{ENUM_NAME}"') == 1
    stub = (root / "src" / "proj" / "m" / "m.pyi").read_text()
    assert stub.count(f"{ENUM_NAME}: tuple[str, ...]") == 1


def test_an_enum_nobody_indexes_is_not_published(tmp_path):
    """A manifest enum no object references adds nothing to the module."""
    root = tmp_path / "proj"
    assert run_cli("new", "proj", cwd=tmp_path).returncode == 0
    manifest = root / "just-makeit.toml"
    manifest.write_text(manifest.read_text() + "\n" + _enum_block())
    assert run_cli("object", "eng", "--state", "mode:int:0", cwd=root)
    stub = (root / "src" / "proj" / "eng.pyi").read_text()
    assert ENUM_NAME not in stub
    ext = (root / "native" / "src" / "eng" / "eng_ext.c").read_text()
    assert ENUM_NAME not in ext

"""gh-2316: an object may decline its Python benchmark.

``no_python_bench = true`` on an object says it has no ``bench_<obj>.py``.
Before it, a project that deleted the file got it back: ``status --check``
failed on it as MISSING, and ``apply`` scaffolded it again, because the
replay both compare against renders one for every object.

The key is manifest-only, so ``apply``'s replay has to carry it -- dropping
it there is the regression these tests are for. Each is run on a standalone
object AND a module object, since the two are written by different code
(``_init`` and ``_object``) and land in different directories.

* ``test_a_deleted_bench_without_the_key_is_missing`` is the control: the
  fixture really does report a deleted benchmark, so the next test's clean
  result is the key's doing.
* ``test_a_declined_bench_stays_gone`` -- ``status --check`` passes and
  ``apply`` writes nothing back.
* ``test_a_declined_bench_still_on_disk_is_reported`` -- the converse, the
  gh-1142 shape: the file is on disk and the manifest says it should not
  be, so ``status --check`` fails naming it, ``apply`` warns, and nothing
  is deleted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli

#: (object, module or "", where its Python benchmark lands).
_OBJECTS = (
    ("fir", "", "src/demo/benchmarks/bench_fir.py"),
    ("biq", "filt", "src/demo/filt/benchmarks/bench_biq.py"),
)


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    """A project with a standalone object and a module object."""
    assert run_cli("new", "demo", cwd=tmp_path).returncode == 0
    root = tmp_path / "demo"
    for args in (("object", "fir"), ("module", "filt")):
        r = run_cli(*args, cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli("object", "biq", "--module", "filt", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    for _, _, rel in _OBJECTS:
        assert (root / rel).is_file(), rel
    return root


def _decline(root: Path, obj: str) -> None:
    """Declare ``no_python_bench`` in *obj*'s fragment, under its table."""
    frag = root / "objects" / f"{obj}.toml"
    text = frag.read_text(encoding="utf-8")
    head = f"[{obj}]\n"
    assert text.count(head) == 1, text
    frag.write_text(
        text.replace(head, head + 'no_python_bench = "true"\n'),
        encoding="utf-8",
    )


@pytest.mark.parametrize("obj,module,rel", _OBJECTS)
def test_a_deleted_bench_without_the_key_is_missing(
    proj: Path, obj: str, module: str, rel: str
) -> None:
    (proj / rel).unlink()
    r = run_cli("status", "--check", cwd=proj)
    assert r.returncode != 0, r.stdout
    assert "MISSING" in r.stdout and rel in r.stdout, r.stdout


@pytest.mark.parametrize("obj,module,rel", _OBJECTS)
def test_a_declined_bench_stays_gone(
    proj: Path, obj: str, module: str, rel: str
) -> None:
    _decline(proj, obj)
    (proj / rel).unlink()
    r = run_cli("status", "--check", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (proj / rel).exists(), r.stdout
    # The key survives apply's rewrite of the manifest.
    frag = (proj / "objects" / f"{obj}.toml").read_text(encoding="utf-8")
    assert 'no_python_bench = "true"' in frag, frag
    r = run_cli("status", "--check", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("obj,module,rel", _OBJECTS)
def test_a_declined_bench_still_on_disk_is_reported(
    proj: Path, obj: str, module: str, rel: str
) -> None:
    _decline(proj, obj)
    before = (proj / rel).read_bytes()
    r = run_cli("status", "--check", cwd=proj)
    assert r.returncode != 0, r.stdout
    assert "DECLINED (1)" in r.stdout, r.stdout
    assert f"  ! {rel}" in r.stdout, r.stdout
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"{rel} is a Python benchmark for an object" in r.stdout, r.stdout
    assert (proj / rel).read_bytes() == before

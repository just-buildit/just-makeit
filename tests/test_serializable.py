"""gh-400: a ``serializable = true`` object flag generates the Python
state-blob binding triplet (``state_bytes`` / ``get_state`` / ``set_state``)
over a hand-written C triplet — the "elastic / pure-transducer" face, sibling
to ``reset``. The C bodies stay hand-written; jm owns only the binding + ``.pyi``
and persists/replays the flag idempotently through the manifest.
"""

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run
from just_makeit._config import (
    add_component,
    is_serializable,
    load,
    save,
)
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _scaffold(tmp_path, *, serializable=True, name="osc"):
    root = tmp_path / "p"
    _silent(new_run, "p", root)
    _silent(
        object_run,
        root,
        name,
        module=None,
        state_vars=[("phase", "double", "0.0")],
        mutable=True,
        serializable=serializable,
    )
    return root


def _ext(root, name="osc"):
    return (root / f"native/src/{name}/{name}_ext.c").read_text(
        encoding="utf-8"
    )


def _pyi(root, name="osc"):
    return (root / f"src/p/{name}.pyi").read_text(encoding="utf-8")


# ── reader ──────────────────────────────────────────────────────────────────


class TestReader:
    def test_truthy(self):
        assert is_serializable({"o": {"serializable": "true"}}, "o")

    def test_default_false(self):
        assert not is_serializable({"o": {}}, "o")
        assert not is_serializable({}, "o")


# ── manifest round-trip (the integration risk: dump must not drop the key) ────


class TestManifestRoundTrip:
    def test_add_component_persists(self, tmp_path):
        root = tmp_path / "p"
        _silent(new_run, "p", root)
        cfg = load(root)
        add_component(
            cfg, "osc", [("phase", "double", "0.0")], serializable_=True
        )
        _silent(save, root, cfg)
        # Reload from disk — the key must survive _dump's scalar whitelist.
        assert is_serializable(load(root), "osc")

    def test_cli_flag_persists(self, tmp_path):
        root = _scaffold(tmp_path, serializable=True)
        assert is_serializable(load(root), "osc")

    def test_off_by_default_not_written(self, tmp_path):
        root = _scaffold(tmp_path, serializable=False)
        # Keeps non-serializable manifests byte-identical — no golden churn.
        toml = "".join(
            p.read_text(encoding="utf-8") for p in root.rglob("*.toml")
        )
        assert "serializable" not in toml
        assert not is_serializable(load(root), "osc")


# ── generated binding ─────────────────────────────────────────────────────────


class TestGeneratedBinding:
    def test_ext_has_triplet(self, tmp_path):
        ext = _ext(_scaffold(tmp_path))
        # static wrappers calling the hand-written C triplet over self->handle
        assert "Osc_state_bytes(OscObject *self" in ext
        assert "osc_state_bytes(self->handle)" in ext
        assert "osc_get_state(self->handle" in ext
        assert "osc_set_state(self->handle" in ext
        # registered in the method table with the right calling conventions
        assert (
            '{"state_bytes", (PyCFunction)Osc_state_bytes, METH_NOARGS,' in ext
        )
        assert '{"get_state", (PyCFunction)Osc_get_state, METH_NOARGS,' in ext
        assert '{"set_state", (PyCFunction)Osc_set_state, METH_O,' in ext

    def test_set_state_validates(self, tmp_path):
        ext = _ext(_scaffold(tmp_path))
        # rejects non-bytes, size mismatch, and a core-rejected blob
        assert "set_state expects bytes" in ext
        assert "state blob size mismatch" in ext
        assert "set_state rejected the blob" in ext

    def test_pyi_has_triplet(self, tmp_path):
        pyi = _pyi(_scaffold(tmp_path))
        assert "def state_bytes(self) -> int:" in pyi
        assert "def get_state(self) -> bytes:" in pyi
        assert "def set_state(self, blob: bytes) -> None:" in pyi

    def test_not_serializable_has_no_triplet(self, tmp_path):
        root = _scaffold(tmp_path, serializable=False)
        assert "state_bytes" not in _ext(root)
        assert "state_bytes" not in _pyi(root)


# ── apply replay: binding comes from the manifest, not the creation flag ───────


class TestApplyReplay:
    def test_apply_regenerates_triplet(self, tmp_path):
        root = _scaffold(tmp_path)
        # Wipe the generated glue, then rebuild it purely from the manifest.
        (root / "native/src/osc/osc_ext.c").unlink()
        _silent(apply_run, root)
        assert "osc_state_bytes(self->handle)" in _ext(root)

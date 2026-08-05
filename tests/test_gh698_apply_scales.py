"""gh-698: `jm apply` must not do O(methods x project) redundant work.

doppler reported `jm apply` hanging — >10 min, zero stdout, no files written —
on their three composing modules. It was not a deadlock and not `depends_on`
depth: apply *replays* one mutating command per method, and each command both
rewrote the whole manifest through tomlkit's comment-preserving path and
regenerated its entire module. Two quadratic terms, so a large project stopped
finishing rather than merely being slow.

Both are removed by doing the redundant work once:

* ``_config.scratch_writes`` — the replay writes into a throwaway tree that it
  just synthesized, so there are no authored comments to preserve. It takes a
  plain dump **only when that dump verifiably round-trips**, because `_dump` is
  hand-written per section kind and silently omitted ``[codec.X]``.
* ``_object.deferred_module_regen`` — coalesces the per-command module
  regeneration to one flush per module.

What is asserted here is the *shape* of the cost, not a wall-clock budget: a
timing threshold on shared CI is a flaky test, whereas "apply does not call
this N times for N methods" is exactly the defect and is deterministic.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _object as O  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

N_OBJECTS = 3
N_METHODS = 6


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    new_run("demo", root)
    module_run(root, "dsp")
    for o in range(N_OBJECTS):
        object_run(
            root,
            f"obj{o}",
            "dsp",
            state_vars=[("t", "double", "1.0")],
            arg_type="double[]",
            return_type="double",
        )
        for m in range(N_METHODS):
            method_run(
                root,
                f"obj{o}",
                f"m{m}",
                "dsp",
                "void",
                "void",
                False,
                [],
                params=[("a", "double")],
            )
    return root


def test_module_is_regenerated_once_not_once_per_method(project, monkeypatch):
    """The dominant term: M objects regenerated N times for N methods."""
    calls: list[str] = []
    real = O._regenerate_module_now

    def counting(root, cfg, module, pkg, *a, **k):
        calls.append(module)
        return real(root, cfg, module, pkg, *a, **k)

    monkeypatch.setattr(O, "_regenerate_module_now", counting)
    apply_run(project)

    total_methods = N_OBJECTS * N_METHODS
    assert len(calls) <= 2, (
        f"module regenerated {len(calls)}x for {total_methods} methods — the "
        f"per-command regeneration is no longer coalesced (gh-698). Each call "
        f"rebuilds every object in the module, so this is O(objects x methods)."
    )


def test_replay_does_not_reparse_the_manifest_per_save(project, monkeypatch):
    """The second term: tomlkit round-trip per mutating command."""
    tomlkit = pytest.importorskip("tomlkit")
    loads = []
    real = tomlkit.loads

    def counting(s, *a, **k):
        loads.append(len(s))
        return real(s, *a, **k)

    monkeypatch.setattr(tomlkit, "loads", counting)
    apply_run(project)

    assert not loads, (
        f"apply re-parsed the manifest {len(loads)}x through tomlkit — the "
        f"scratch-write fast path is not engaged (gh-698). The replay tree has "
        f"no authored comments to preserve, so this is pure O(manifest size) "
        f"work per method."
    )


def test_scratch_writes_falls_back_when_the_dump_is_lossy():
    """The fast path must verify, not assume.

    `_dump` silently omits `[codec.X]`. Using it unconditionally dropped
    codecs from the replayed manifest, and every later replay step then failed
    to resolve them. The round-trip check is what makes the fast path safe for
    section kinds `_dump` does not know about.
    """
    lossy = {"project": {"name": "d"}, "codec": {"kw": {"kind": "pack"}}}
    text = C._dump(lossy)
    assert "codec" not in text, "if _dump learned codecs, simplify this guard"
    assert not C._round_trips(text, lossy, None)


def test_round_trip_accepts_a_faithful_dump():
    cfg = {"project": {"name": "demo", "version": "0.1.0"}}
    assert C._round_trips(C._dump(cfg), cfg, None)


def test_scratch_writes_is_scoped(tmp_path):
    """It must not leak past the replay — an ordinary command still preserves
    comments."""
    assert C._SCRATCH_WRITES is False
    with C.scratch_writes():
        assert C._SCRATCH_WRITES is True
    assert C._SCRATCH_WRITES is False


def test_deferred_regen_is_scoped():
    assert O._DEFERRED_REGEN is None
    with O.deferred_module_regen():
        assert O._DEFERRED_REGEN == {}
    assert O._DEFERRED_REGEN is None


def test_apply_is_still_a_fixed_point(project):
    """Whatever the speedup, a second apply must change nothing."""
    apply_run(project)
    before = {
        p.relative_to(project): p.read_bytes()
        for p in sorted(project.rglob("*"))
        if p.is_file() and "compile_commands" not in p.name
    }
    apply_run(project)
    after = {
        p.relative_to(project): p.read_bytes()
        for p in sorted(project.rglob("*"))
        if p.is_file() and "compile_commands" not in p.name
    }
    assert before == after

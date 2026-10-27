"""gh-2215: a cycle of `object` init-param references is refused at load.

A consumer constructed from a producer's capsule keeps the producer alive
through a strong reference (gh-790's ``_<name>_owner``), and no type jm
generates has a ``tp_traverse``, so objects whose references form a cycle
are never collected. A cycle has no construction order either, which is how
review found it: `jm script` must create every producer before its
consumers. So `_config.load` refuses one -- every command passes through it
-- and `_config.save` refuses the write that would close one.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
from _jmrun import run_cli

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _ok(cwd: Path, *argv: str) -> None:
    r = run_cli(*argv, cwd=cwd)
    assert r.returncode == 0, (argv, r.stdout + r.stderr)


def _producers(tmp_path: Path, *names: str) -> Path:
    """A project whose objects each publish a capsule; no references yet."""
    _ok(tmp_path, "new", "p")
    root = tmp_path / "p"
    for n in names:
        _ok(root, "object", n)
        _ok(root, "property", n, "_capsule", "--type", "capsule",
            "--capsule", f"p.{n}")  # fmt: skip
    return root


def _hand_edge(root: Path, consumer: str, producer: str) -> None:
    """Append a reference to *consumer*'s fragment, as an author would."""
    frag = root / "objects" / f"{consumer}.toml"
    with frag.open("a", encoding="utf-8") as fh:
        fh.write(
            f'\n[[{consumer}.init_params]]\nname = "{producer}_src"\n'
            f'object = "{producer}"\nrequired = true\n'
        )


def _digest(root: Path) -> "dict[str, str]":
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


CYCLES = {
    "two": (("a", "b"), "a -> b -> a"),
    "three": (("a", "b", "c"), "a -> b -> c -> a"),
}


@pytest.mark.parametrize("ring", sorted(CYCLES))
def test_a_hand_written_cycle_is_refused_on_load(
    tmp_path: Path, ring: str
) -> None:
    names, named = CYCLES[ring]
    root = _producers(tmp_path, *names)
    for i, n in enumerate(names):
        _hand_edge(root, n, names[(i + 1) % len(names)])
    before = _digest(root)
    # `script` writes nothing, so only `load` stands between it and the
    # cycle; `apply` and `status` would meet `save`'s refusal on the replay.
    for cmd in ("apply", "status", "script"):
        r = run_cli(cmd, cwd=root)
        assert r.returncode != 0, cmd
        assert named in r.stderr, r.stderr
        assert "retain = true" in r.stderr
        assert "Traceback" not in r.stderr
    assert _digest(root) == before


@pytest.mark.parametrize("ring", sorted(CYCLES))
def test_the_command_closing_a_cycle_is_refused(
    tmp_path: Path, ring: str
) -> None:
    """Every edge but `a`'s is in the manifest, naming an `a` that does not
    exist yet; `jm object a --init-param ...:object:b` closes the ring.
    The cycle is named from the first member the manifest lists -- `b`,
    since `a` is the one being added."""
    names, _ = CYCLES[ring]
    named = " -> ".join([*names[1:], names[0], names[1]])
    root = _producers(tmp_path, *names[1:])
    for i, n in enumerate(names[1:], start=1):
        _hand_edge(root, n, names[(i + 1) % len(names)])
    before = _digest(root)
    r = run_cli("object", "a", "--init-param", "a_src:object:b", cwd=root)
    assert r.returncode != 0
    assert named in r.stderr, r.stderr
    assert "Traceback" not in r.stderr
    assert _digest(root) == before


def test_a_diamond_is_not_a_cycle(tmp_path: Path) -> None:
    """Two consumers of one producer: shared, not circular."""
    root = _producers(tmp_path, "c")
    for n in ("a", "b"):
        _ok(root, "object", n, "--init-param", "src:object:c")
    _ok(root, "apply")
    assert run_cli("status", "--check", cwd=root).returncode == 0


def test_a_views_reference_closes_a_cycle_too() -> None:
    """A view's own init_params name a producer as the object's do
    (gh-504), and a reference may name a view class: both are edges."""
    from just_makeit import _config as C

    cfg = {
        "project": {"name": "p"},
        "a": {"init_params": [{"name": "s", "object": "b.BView"}]},
        "b": {
            "views": [
                {
                    "class_name": "BView",
                    "create_fn": "b_create_view",
                    "init_params": [{"name": "s", "object": "a"}],
                }
            ]
        },
    }
    (refusal,) = C.object_ref_cycles(cfg)
    assert "a -> b -> a" in refusal

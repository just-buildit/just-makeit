"""gh-1479: removing a module's last function leaves no orphaned harness.

A module's free functions are its core (gh-1034); `jm remove function` of
the last one ends the core, and the regenerated CMakeLists stops building it.
Its C test, symbols test and bench stayed on disk -- compiled by nothing,
exercising functions that were gone -- so `status --check` failed UNBUILT on
the tree `jm remove` itself had just produced (the `jm_remove` example's
Gate A ratchet line). They now go as an object's do on `jm remove object`.

GATE: after `jm remove function` of a module's last function, the module
      core's C test and bench are gone and `status --check` reports nothing
      UNBUILT; a module whose name is also one of its objects keeps them.
"""

from __future__ import annotations

from pathlib import Path

from _jmrun import run_cli


def _harness(root: Path, cname: str) -> "list[Path]":
    return [
        root / "native" / "tests" / f"test_{cname}_core.c",
        root / "native" / "benchmarks" / f"bench_{cname}_core.c",
    ]


def _module_with_one_function(tmp_path: Path, obj: str) -> Path:
    for argv in (
        ("new", "p"),
        ("module", "synth"),
        ("object", obj, "--module", "synth"),
        ("function", "detune", "--module", "synth", "--param", "x:double",
         "--return-type", "double"),
    ):  # fmt: skip
        cwd = tmp_path if argv[0] == "new" else tmp_path / "p"
        r = run_cli(*argv, cwd=cwd)
        assert r.returncode == 0, (argv, r.stdout + r.stderr)
    return tmp_path / "p"


def test_the_last_functions_harness_goes_with_it(tmp_path):
    root = _module_with_one_function(tmp_path, "env")
    assert all(p.is_file() for p in _harness(root, "synth"))
    r = run_cli("remove", "function", "detune", "--module", "synth",
                "--force", cwd=root)  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert not any(p.exists() for p in _harness(root, "synth"))
    status = run_cli("status", cwd=root)
    assert "UNBUILT" not in status.stdout, status.stdout


def test_an_object_named_like_its_module_keeps_its_harness(tmp_path):
    root = _module_with_one_function(tmp_path, "synth")
    r = run_cli("remove", "function", "detune", "--module", "synth",
                "--force", cwd=root)  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert all(p.is_file() for p in _harness(root, "synth"))

"""gh-963: a mutating verb must not need `--module` to find the module.

The manifest records which module owns an object, and an object belongs to at
most one. `--module` was nevertheless the *only* thing five verbs consulted,
so omitting it did not produce an error — it produced the **standalone** code
path on a module-owned object.

What that looked like, end to end, before the fix:

    jm new p && jm module filt && jm object gain --module filt
    jm method gain scale --arg-type float --return-type float
    # -> "Done!  Implement gain_scale() in gain_core.c"

The verb wrote the C stub and the benchmark, never touched the module's
binding fragment, and exited 0. The project then **builds and imports**, and
`Gain` has no `.scale()`. Measured: `hasattr(Gain, "scale")` was False on a
tree that compiled cleanly. The only signal anywhere was `jm status` reporting
drift, and nothing prompts a user to run `status` after a command that said
`Done!`.

`jm regenerate` was the one verb in the family that behaved, because it
resolved the owner from the manifest via `C.component_module`. The fix is that
primitive, shared: `C.resolve_module(cfg, component, declared)`. Passing
`--module` still works and is still validated against the manifest.

**`jm add` is NOT covered here** and is not fixed by this. It fails on a module
object for an unrelated mechanism — the fragment's `Gain_init` body is
preserved as author-owned across regeneration (gh-729/gh-770), so a state
change, which rewrites the constructor's `kwlist` *inside* that body, cannot
land. Verified rather than assumed: deleting the fragment and running
`jm apply` produces the correct `kwlist[] = {"gain", "bias", NULL}`, so
preservation is what blocks it. Tracked as gh-965; `status` already reports
that case as KWARGS drift with its own explanation (gh-612).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SRC = Path(__file__).parent.parent / "src"


def _cli(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from just_makeit._cli import main; main()",
            *args,
        ],
        cwd=cwd,
        env={**os.environ, "PYTHONPATH": str(SRC)},
        capture_output=True,
        text=True,
        timeout=600,
    )


def _module_project(tmp_path: Path, obj: str = "gain") -> Path:
    """A project whose object lives in module `filt`."""
    assert _cli("new", "p", cwd=tmp_path).returncode == 0
    proj = tmp_path / "p"
    assert _cli("module", "filt", cwd=proj).returncode == 0
    assert _cli("object", obj, "--module", "filt", cwd=proj).returncode == 0
    assert _cli("status", "--check", cwd=proj).returncode == 0, "fixture dirty"
    return proj


def _fragment(proj: Path) -> str:
    return (proj / "native/src/filt/filt_ext_gain.c").read_text(
        encoding="utf-8"
    )


# (verb, argv without --module, a symbol the module fragment must gain)
_VERBS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "method",
        (
            "method",
            "gain",
            "scale",
            "--arg-type",
            "float",
            "--return-type",
            "float",
        ),
        "Gain_scale",
    ),
    (
        "property",
        ("property", "gain", "level", "--type", "double"),
        "level",
    ),
    (
        "warning",
        ("warning", "gain", "--condition", "gain", "--message", "hi"),
        "PyErr_WarnEx",
    ),
    (
        "error",
        ("error", "gain", "--category", "ValueError", "--message", "boom"),
        '"boom"',
    ),
)


@pytest.mark.parametrize(
    "verb,argv,symbol", _VERBS, ids=[v[0] for v in _VERBS]
)
def test_the_verb_reaches_the_module_binding_without_the_flag(
    tmp_path, verb, argv, symbol
):
    """The defect itself: the member has to land in the module's fragment.

    Asserted on the fragment rather than only on `status`, because `status`
    reporting drift is the *symptom* a user never sees — the bug is that the
    generated extension does not carry the member.
    """
    proj = _module_project(tmp_path)
    before = _fragment(proj)
    r = _cli(*argv, cwd=proj)
    assert r.returncode == 0, f"{verb} failed:\n{r.stdout}\n{r.stderr}"
    after = _fragment(proj)
    assert symbol in after and symbol not in before, (
        f"`jm {verb}` without --module did not reach the module binding: "
        f"{symbol!r} is absent from filt_ext_gain.c. The verb took the "
        f"standalone path on a module-owned object."
    )


@pytest.mark.parametrize(
    "verb,argv,symbol", _VERBS, ids=[v[0] for v in _VERBS]
)
def test_the_verb_leaves_the_tree_clean_without_the_flag(
    tmp_path, verb, argv, symbol
):
    """A verb must not leave the tree in a state its own drift gate rejects.

    `jm status --check` is documented as a drop-in CI gate. Exiting 0 from a
    command that makes it exit 1, with `Done!` on stdout, means the next commit
    fails CI for something the author was not told about.
    """
    proj = _module_project(tmp_path)
    assert _cli(*argv, cwd=proj).returncode == 0
    r = _cli("status", "--check", cwd=proj)
    assert r.returncode == 0, (
        f"`jm {verb}` without --module left the tree dirty:\n{r.stdout}"
    )


@pytest.mark.parametrize(
    "verb,argv,symbol", _VERBS, ids=[v[0] for v in _VERBS]
)
def test_passing_the_flag_is_still_equivalent(tmp_path, verb, argv, symbol):
    """Inference must agree with the explicit flag, not merely work.

    If the two produced different trees, `--module` would have become a second
    way to say the same thing that says something else — and `_apply`'s replay
    always passes it explicitly, so a divergence here is a tree that `apply`
    would rewrite on the next run.
    """
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    inferred = _module_project(tmp_path / "a")
    assert _cli(*argv, cwd=inferred).returncode == 0
    explicit = _module_project(tmp_path / "b")
    assert _cli(*argv, "--module", "filt", cwd=explicit).returncode == 0
    assert _fragment(inferred) == _fragment(explicit)


def test_an_unknown_module_is_still_rejected(tmp_path):
    """Inference must not swallow a wrong `--module`.

    The flag's existing validation — the object must actually be in the module
    named — has to survive, or a typo silently retargets the command.
    """
    proj = _module_project(tmp_path)
    r = _cli(
        "method",
        "gain",
        "scale",
        "--module",
        "nosuch",
        "--arg-type",
        "float",
        "--return-type",
        "float",
        cwd=proj,
    )
    assert r.returncode != 0
    assert "not found in module" in r.stderr


def test_a_standalone_object_is_unaffected(tmp_path):
    """The regression risk: `resolve_module` returns None for standalone.

    That is exactly the value `--module`'s absence carried before, so the
    standalone paths must be byte-identical. Asserted on the generated glue,
    not just on the exit code.
    """
    assert _cli("new", "p", cwd=tmp_path).returncode == 0
    proj = tmp_path / "p"
    assert _cli("object", "solo", cwd=proj).returncode == 0
    assert (
        _cli(
            "method",
            "solo",
            "scale",
            "--arg-type",
            "float",
            "--return-type",
            "float",
            cwd=proj,
        ).returncode
        == 0
    )
    ext = (proj / "native/src/solo/solo_ext.c").read_text(encoding="utf-8")
    assert "Solo_scale" in ext
    assert _cli("status", "--check", cwd=proj).returncode == 0


_HAS_TOOLCHAIN = shutil.which("cmake") and any(
    shutil.which(c) for c in ("cc", "gcc", "clang")
)


@pytest.mark.skipif(not _HAS_TOOLCHAIN, reason="no C toolchain")
def test_the_method_actually_exists_on_the_built_class(tmp_path):
    """The whole property, not a proxy for it.

    Every other assertion here reads generated text. This one compiles the
    project and calls the method, because the reported failure was a tree that
    built and imported perfectly well with the member simply absent — which no
    amount of reading the right file would have caught if the render had been
    right and the *wiring* wrong.
    """
    proj = _module_project(tmp_path)
    assert (
        _cli(
            "method",
            "gain",
            "scale",
            "--arg-type",
            "float",
            "--return-type",
            "float",
            cwd=proj,
        ).returncode
        == 0
    )

    core = proj / "native/src/gain/gain_core.c"
    body = core.read_text(encoding="utf-8")
    stub = "    (void)state; (void)x;\n    return (float)0.0f;"
    assert stub in body, "the generated stub changed shape; fixture needs it"
    core.write_text(
        body.replace(stub, "    (void)state;\n    return x * 2.0f;"),
        encoding="utf-8",
    )

    build = subprocess.run(
        ["make"], cwd=proj, capture_output=True, text=True, timeout=900
    )
    assert build.returncode == 0, f"build failed:\n{build.stdout[-3000:]}"

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, 'src')\n"
            "from p.filt import Gain\n"
            "g = Gain(gain=1.0)\n"
            "print(hasattr(Gain, 'scale'), g.scale(3.0))",
        ],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert probe.returncode == 0, f"import failed:\n{probe.stderr}"
    assert probe.stdout.split() == ["True", "6.0"], (
        f"the built class does not carry a working .scale(): {probe.stdout!r}"
    )


def test_status_json_confirms_no_drift_category_is_hiding_it(tmp_path):
    """`status --check`'s exit code is one bit; check the categories too.

    A finding reported in a non-gating category (`unreconciled`, `kwargs`)
    would leave the exit code at 0 while the tree is still wrong — which is
    exactly how gh-963's `add` case presents. This asserts the fixed verbs land
    in none of them.
    """
    proj = _module_project(tmp_path)
    assert (
        _cli(
            "method",
            "gain",
            "scale",
            "--arg-type",
            "float",
            "--return-type",
            "float",
            cwd=proj,
        ).returncode
        == 0
    )
    r = _cli("status", "--json", cwd=proj)
    payload = json.loads(r.stdout)
    assert [e for e in payload["entries"] if e["state"] != "ok"] == []
    assert payload["kwargs_drift"] == []

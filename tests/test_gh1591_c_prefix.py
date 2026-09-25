"""gh-1591 phase 2: `[project] c_prefix` namespaces every C symbol jm derives.

Two installed jm packages that share a component name collide today: at link
time, and in one translation unit, where the shared include guard silently
drops the second header. `c_prefix` puts one namespace on every identifier
jm DERIVES from a name -- lifecycle functions, methods, accessors, module
functions, `<comp>_state_t`, jm's include guards and `process_global`
defines -- and on nothing the author named.

The monkeypatched oracle (tests/test_gh1633_*) proved every derived symbol
routes through `_csym.stem`. This is the same broad fixture with a REAL key
in the manifest: `jm new --c-prefix zz`, then the CLI, then `apply` -- the
path a user takes, replay included.

GATE: a `c_prefix` project renders every derived C identifier prefixed and
      nothing else differently, never prefixes an author-named symbol, and
      `apply` refuses two names deriving one symbol, an existing tree whose
      author C still spells the unprefixed names, and a prefix that is not
      an identifier.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import _csym_fixtures as FX
from _jmrun import run_cli
from just_makeit import _config as C

MARK = FX.MARK
PREFIX = MARK.rstrip("_")


@pytest.fixture(scope="module")
def trees(tmp_path_factory):
    plain = FX.build(tmp_path_factory.mktemp("plain"))
    pre = FX.build(tmp_path_factory.mktemp("pre"), "--c-prefix", PREFIX)
    return plain, pre


def test_the_manifest_carries_the_key(trees):
    _plain, pre = trees
    for row, root in pre.items():
        assert C.c_prefix(C.load(root)) == PREFIX, row


def test_no_file_is_renamed(trees):
    plain, pre = trees
    for row in plain:
        assert sorted(FX.tree(pre[row])) == sorted(FX.tree(plain[row])), row


def test_the_prefix_is_the_only_difference(trees):
    plain, pre = trees
    bad = []
    for row in plain:
        a, b = FX.tree(plain[row]), FX.tree(pre[row])
        for rel in sorted(a.keys() & b.keys() - {"just-makeit.toml"}):
            if FX.stripped(b[rel]) != a[rel]:
                bad.append(f"{row}/{rel}")
    assert bad == [], (
        "with c_prefix these files differ in more than the prefix:\n"
        + "\n".join(bad)
    )


def test_no_derived_symbol_escapes_the_prefix(trees):
    _plain, pre = trees
    bad = [
        f"{row}/{ln}" for row, root in pre.items() for ln in FX.escapes(root)
    ]
    assert bad == [], (
        "a C identifier jm derives from a name kept its unprefixed spelling "
        "under a real c_prefix:\n" + "\n".join(bad)
    )


def test_an_author_named_symbol_is_never_prefixed(trees):
    _plain, pre = trees
    checked = 0
    bad = []
    for row, root in pre.items():
        names = FX.author_names(C.load(root))
        code = "\n".join(
            p.read_text(encoding="utf-8")
            for p in sorted((root / "native").rglob("*"))
            if p.suffix in (".c", ".h")
        )
        for n in sorted(names):
            if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(n)}\b", code):
                continue
            checked += 1
            if re.search(rf"\b{re.escape(MARK + n)}\b", code):
                bad.append(f"{row}: {MARK}{n}")
    # Not vacuous: the fixture's view, handle and composer name several.
    assert checked >= 4, checked
    assert bad == [], "an author-named symbol was prefixed:\n" + "\n".join(bad)


# ── the refusals ─────────────────────────────────────────────────────────────


def _new(tmp_path: Path, name: str, *args: str) -> Path:
    r = run_cli("new", name, *args, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    return tmp_path / name


def _run(root: Path, *args: str) -> None:
    r = run_cli(*args, cwd=root)
    assert r.returncode == 0, (args, r.stdout + r.stderr)


def test_a_name_that_carries_the_prefix_is_not_prefixed_again(tmp_path):
    root = _new(tmp_path, "p", "--c-prefix", "dp", "--object", "dp_tlm")
    _run(root, "apply")
    h = next((root / "native" / "inc").rglob("dp_tlm_core.h")).read_text()
    assert "dp_tlm_state_t *dp_tlm_create(" in h, h
    assert "dp_dp_" not in h and "DP_DP_" not in h


def test_two_names_deriving_one_symbol_are_refused(tmp_path):
    root = _new(tmp_path, "p", "--c-prefix", "zz", "--object", "x")
    _run(root, "object", "zz_x")
    r = run_cli("apply", cwd=root)
    assert r.returncode == 1, r.stdout
    assert "`zz_x_create` is derived twice" in r.stderr, r.stderr
    assert "`x`" in r.stderr and "`zz_x`" in r.stderr, r.stderr


def test_a_method_and_a_module_function_deriving_one_symbol_are_refused(
    tmp_path,
):
    root = _new(tmp_path, "p", "--c-prefix", "zz", "--object", "fir")
    _run(root, "method", "fir", "scale", "--arg-type", "double")
    _run(root, "module", "util")
    _run(root, "function", "fir_scale", "--module", "util")
    r = run_cli("apply", cwd=root)
    assert r.returncode == 1, r.stdout
    assert "`zz_fir_scale` is derived twice" in r.stderr, r.stderr


def test_names_that_only_share_a_stem_are_not_a_collision(tmp_path):
    root = _new(tmp_path, "p", "--c-prefix", "zz", "--object", "fir")
    _run(root, "object", "fir_bank")
    _run(root, "apply")
    assert run_cli("status", "--check", cwd=root).returncode == 0


def test_an_existing_tree_that_adds_the_key_is_refused(tmp_path):
    """Its sacred C still spells the unprefixed names, and would not link
    against the render. Case-sensitive and whole identifiers: the author's
    own `FIR_STATE_MAGIC` beside the derived `fir_state_t` is not jm's."""
    root = _new(tmp_path, "old", "--no-c-prefix", "--object", "fir")
    h = next((root / "native" / "inc").rglob("fir_core.h"))
    h.write_text(
        h.read_text().replace(
            "typedef struct", "#define FIR_STATE_MAGIC 0xF1\ntypedef struct", 1
        )
    )
    toml = root / "just-makeit.toml"
    toml.write_text(
        toml.read_text().replace(
            "[project]\n", '[project]\nc_prefix = "zz"\n', 1
        )
    )
    before = FX.tree(root)
    r = run_cli("apply", cwd=root)
    assert r.returncode == 1, r.stdout
    for rel in (
        "fir/fir_core.h",
        "native/src/fir/fir_core.c",
        "native/tests/test_fir_core.c",
    ):
        assert rel in r.stderr, (rel, r.stderr)
    assert "`fir_state_t`" in r.stderr and "`fir_create`" in r.stderr
    assert "`FIR_CORE_H`" in r.stderr
    assert "FIR_STATE_MAGIC" not in r.stderr, r.stderr
    assert "jm upgrade" in r.stderr
    assert FX.tree(root) == before, "a refused apply wrote files"
    assert run_cli("status", "--check", cwd=root).returncode == 1


@pytest.mark.parametrize("bad", ["dp_", "1x", "a-b", ""])
def test_a_prefix_that_is_not_an_identifier_is_refused(tmp_path, bad):
    r = run_cli("new", "p", "--c-prefix", bad, cwd=tmp_path)
    assert r.returncode == 1
    assert "c_prefix" in r.stderr, r.stderr
    assert not (tmp_path / "p").exists()


def test_jm_script_replays_the_prefix(tmp_path):
    root = _new(tmp_path, "p", "--c-prefix", "zz", "--object", "fir")
    r = run_cli("script", cwd=root)
    assert r.returncode == 0, r.stderr
    new_line = r.stdout[r.stdout.index("just-makeit new") :].split("cd p")[0]
    assert "--c-prefix zz" in new_line.replace("\\\n", " ").replace("  ", " ")

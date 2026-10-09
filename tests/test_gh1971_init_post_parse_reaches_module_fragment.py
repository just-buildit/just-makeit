"""gh-1971: `init_post_parse` reaches the fragment of an object in a module.

`[<obj>] init_post_parse` is the manifest's way to write a dynamic default:
"an inline C snippet injected after ``PyArg_ParseTupleAndKeywords``"
(`_config.init_post_parse`). For an object in a module with per-object
``<mod>_ext_<obj>.c`` fragments it was accepted and then ignored: no
diagnostic, nothing in `status`. Two places dropped it.

* `_object.build_component_ctxs` builds the fragment's context and called
  `_make_object_ctx` without `init_post_parse_impl`, so `make_state_ctx` got
  its default ``""``.
* The module path renders from the replay's TEMP manifest, and
  `_config.add_component` had no parameter to persist the key into it. That is
  gh-1172's shape exactly (a manifest-only key the replay never wrote): fixing
  only the first drop changes nothing, because the key is absent from the
  manifest the first drop would read.

That is the dangerous shape for `adopt`. A fragment that carries the snippet by
hand is reported as a `differs` unit, and `--accept` takes it, because the
render is the authority. The render was missing the very line the manifest
declares, so a clean-looking adoption deleted a default the project depended on
(doppler's `PN`: `poly == 0` selects the maximal-length polynomial).

Why `status --check` could not see it
-------------------------------------
`status` renders the project through the same builder and compares the result
with the disk. A key that builder drops is dropped on both sides, so the two
agree. These tests therefore read the FRAGMENT TEXT for the snippet instead of
asking `status` whether anything drifted: the only reference that does not
inherit the omission is the manifest itself.

Scope. This covers an object in a module. A standalone object rendered by
delete-and-`apply` was also observed to lose the key; that path is not changed
here and has no test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli

# Four-space indented, as the generated `tp_init` body is. The text is the
# user's; jm only has to place it.
SNIPPET = "    if (poly == 0)\n        poly = pn_mls_poly (length);\n"
NEEDLE = "poly = pn_mls_poly (length);"

FRAGMENT = Path("native/src/dsp/dsp_ext_pn.c")


def _declare(root: Path) -> None:
    """Write `init_post_parse` onto the object's own table in its manifest.

    Anchoring on the `[pn]` header puts the key on the object table; appending
    to the file would land it in whatever sub-table is last (`[[pn.init_params]]`
    here), where it means something else -- a real way to mis-reproduce this.
    """
    p = root / "objects" / "pn.toml"
    body = p.read_text(encoding="utf-8")
    assert body.count("[pn]\n") == 1, body
    key = 'init_post_parse = """\n' + SNIPPET + '"""\n'
    p.write_text(body.replace("[pn]\n", "[pn]\n" + key, 1), "utf-8")


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    """A module holding a no-state object with two scalar init params.

    The shape doppler's `PN` has. The key means nothing to an object with no
    constructor arguments, so a bare `--no-state` object would not exercise it.
    """
    assert run_cli("new", "q", "--no-c-prefix", cwd=tmp_path).returncode == 0
    root = tmp_path / "q"
    assert run_cli("module", "dsp", cwd=root).returncode == 0
    r = run_cli(
        "object",
        "pn",
        "--module",
        "dsp",
        "--no-state",
        "--init-param",
        "poly:uint64_t:0",
        "--init-param",
        "length:uint32_t:0",
        cwd=root,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return root


def _fragment_text(root: Path) -> str:
    frag = root / FRAGMENT
    assert frag.is_file(), sorted(
        str(p.relative_to(root)) for p in (root / "native").rglob("*.c")
    )
    return frag.read_text(encoding="utf-8")


def _plant_by_hand(root: Path) -> None:
    """Put the snippet in the fragment the way a hand-patched project has it.

    Immediately before the `create` call, which is where the generated
    `tp_init` places it once the key is honoured.
    """
    frag = root / FRAGMENT
    text = frag.read_text(encoding="utf-8")
    anchor = "    self->handle = pn_create(poly, length);"
    assert text.count(anchor) == 1, "fixture anchor moved"
    frag.write_text(text.replace(anchor, SNIPPET + anchor), encoding="utf-8")


def test_the_snippet_reaches_a_module_objects_fragment(proj: Path) -> None:
    _declare(proj)
    r = run_cli("adopt", "pn", "--accept-additions", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr

    text = _fragment_text(proj)
    assert NEEDLE in text, (
        "the manifest's `init_post_parse` did not reach the fragment -- the "
        "key is accepted and silently ignored on the module path:\n" + text
    )


def test_the_snippet_sits_between_the_parse_and_the_create(proj: Path) -> None:
    """Placement is the contract: after the args exist, before they are used."""
    _declare(proj)
    r = run_cli("adopt", "pn", "--accept-additions", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr

    text = _fragment_text(proj)
    parsed = text.index("PyArg_ParseTupleAndKeywords")
    converted = text.index("uint32_t length")
    snippet = text.index(NEEDLE)
    created = text.index("pn_create")
    assert parsed < converted < snippet < created


def test_a_hand_patched_fragment_adopts_without_losing_the_snippet(
    proj: Path,
) -> None:
    """The case that made the bug dangerous, not merely annoying.

    A fragment carrying the snippet by hand, and a manifest declaring the same
    snippet. With the key honoured the render IS the hand-written text, so
    `adopt` has nothing to remove and flips it. Without it the render lacks
    the snippet, the unit shows as `differs`, and `--accept` -- which `adopt`
    invites for a `differs` unit -- deletes the behaviour the manifest line
    was written to carry.
    """
    _plant_by_hand(proj)
    _declare(proj)

    r = run_cli("adopt", "pn", cwd=proj)
    assert r.returncode == 0, (
        "adopt refused a fragment whose hand-written snippet the manifest "
        "declares identically -- the render is missing it:\n"
        + r.stdout
        + r.stderr
    )
    assert NEEDLE in _fragment_text(proj)


def test_the_adopted_fragment_is_not_drift(proj: Path) -> None:
    """The render and the disk agree once the key is honoured."""
    _declare(proj)
    r = run_cli("adopt", "pn", "--accept-additions", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli("status", "--check", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr

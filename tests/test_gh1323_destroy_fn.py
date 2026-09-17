"""The destructor is the counterpart of the declared constructor (gh-1323).

`create_fn` names the C jm calls to CONSTRUCT. Nothing named its counterpart,
so the dealloc path emitted `<comp>_destroy` whoever built the thing — one
declaration, one direction, and the two halves of an object's lifetime decided
by different things.

Not a naming inconvenience. doppler's ring `create` builds a double-mapped
region (`memfd_create` + two `mmap`s of the same pages) and its destroyer
unmaps it; jm's scaffolded counterpart is `free(state)` on a pointer that was
never `malloc`'d, with the mapping never released.

**Which failure you get depends on the shape, and the quiet one is the
dangerous one.** Under `header_only` nothing defines that name, so the author
hits `implicit declaration of 'f32_buffer_destroy'` — loud, at compile. An
ordinary component gets the `free()` version scaffolded into `_core.c`, so it
builds, links, and is wrong at runtime.

The gate is the one gh-1323 asks for: **the constructor and destructor the
binding calls share a prefix**, which catches the mismatch without compiling
anything.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._context import _destroy as D  # noqa: E402


def _dealloc(component="f32_buffer", spec=None, create_fn=""):
    return D.make_destroy_ctx(
        component, "F32Buffer", spec or {}, [], create_fn=create_fn
    )["destroy_dealloc_call"]


class TestTheResolver:
    def test_derived_from_create_fn(self):
        assert D.c_fn("f32_buffer", {}, "dp_f32_create") == "dp_f32_destroy"

    def test_an_explicit_fn_wins(self):
        assert (
            D.c_fn("f32_buffer", {"fn": "dp_f32_release"}, "dp_f32_create")
            == "dp_f32_release"
        )

    def test_no_create_fn_keeps_the_historical_name(self):
        """Byte-identical for every existing project is what makes this safe
        to introduce."""
        assert D.c_fn("engine", {}, "") == "engine_destroy"

    def test_a_creator_that_is_not_prefix_create_is_not_guessed(self):
        """`spawn_engine` gives no prefix to derive from. Guessing
        `spawn_destroy` would be worse than the historical default, because it
        names something plausible that does not exist."""
        assert D.c_fn("engine", {}, "spawn_engine") == "engine_destroy"


class TestTheBindingCallsIt:
    def test_the_dealloc_path_uses_the_derived_name(self):
        assert "dp_f32_destroy(self->handle)" in _dealloc(
            create_fn="dp_f32_create"
        )

    def test_the_fallible_teardown_uses_it_too(self):
        """`returns = "int"` renders a different body; both must agree about
        which function they call, or `close()` and `tp_dealloc` destroy the
        object two different ways."""
        ctx = D.make_destroy_ctx(
            "wfm_writer",
            "WfmWriter",
            {"returns": "int", "name": "close"},
            [],
            create_fn="dp_wfm_create",
        )
        assert (
            "int rc = dp_wfm_destroy(self->handle);"
            in (ctx["destroy_method_body"])
        )

    def test_undeclared_is_unchanged(self):
        assert "f32_buffer_destroy(self->handle)" in _dealloc()


class TestTheCreatorAndDestroyerShareAPrefix:
    """gh-1323's own suggested gate, and it needs no compiler.

    A render-only test could not catch the original defect by looking at
    either name alone -- both render fine, they simply are not the same
    function. The relationship is the thing to assert.
    """

    @pytest.mark.parametrize(
        "create_fn",
        ["dp_f32_create", "acq_create", "dp_iq16_create"],
    )
    def test_they_share_a_prefix(self, create_fn):
        prefix = create_fn[: -len("_create")]
        assert D.c_fn("anything", {}, create_fn).startswith(prefix)

    def test_a_declared_fn_is_exempt_and_deliberately_so(self):
        """An author who names BOTH has said the pair is asymmetric on
        purpose -- `dp_f32_create` / `dp_f32_release` is a real C idiom. The
        gate is about what jm INFERS, not about what it is told."""
        assert (
            D.c_fn("c", {"fn": "unrelated_free"}, "dp_f32_create")
            == "unrelated_free"
        )


class TestTheKeyIsAccepted:
    def test_fn_is_a_known_destroy_key(self):
        D.validate_destroy_spec("c", {"fn": "dp_f32_destroy"}, [])

    def test_an_unknown_key_is_still_refused(self):
        """Adding `fn` must not widen the table to anything."""
        with pytest.raises(ValueError):
            D.validate_destroy_spec("c", {"destroy_fn": "x"}, [])


# ── the derivation has to REACH the render (gh-1326) ────────────────────────
#
# gh-1323's resolver was correct in isolation and the suite above proved it,
# while `jm apply` still emitted `<comp>_destroy`: `make_destroy_ctx` takes
# `create_fn` as a keyword and THREE call sites did not pass it. A capability
# computed and then dropped in transit, which a unit test of the resolver
# cannot see -- doppler found it adopting v0.76.0.
#
# So this gate asks the question of every caller rather than of the resolver:
# any site that can know `create_fn` must hand it over.


def _destroy_ctx_call_sites():
    """Every `make_destroy_ctx(...)` call in the tree, with its source text."""
    import ast

    root = Path(__file__).parent.parent / "src" / "just_makeit"
    for path in sorted(root.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        if "make_destroy_ctx(" not in src:
            continue
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            nm = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if nm != "make_destroy_ctx":
                continue
            yield path, node, ast.get_source_segment(src, node) or ""


class TestEveryCallerPassesCreateFn:
    def test_the_sweep_is_armed(self):
        sites = list(_destroy_ctx_call_sites())
        assert len(sites) >= 6, sites

    def test_a_site_that_can_know_create_fn_passes_it(self):
        """The exemption is narrow and stated: a site with no manifest and no
        `create_fn` in scope (a bare scaffold render, `jm bind`) genuinely
        cannot know, and passes nothing. Anything that mentions `cfg` or has
        `create_fn` available must pass it, or the derivation silently does not
        happen for that path.
        """
        missing = []
        for path, node, text in _destroy_ctx_call_sites():
            kwargs = {k.arg for k in node.keywords}
            if "create_fn" in kwargs:
                continue
            # a site that reads the manifest can always look it up
            if "cfg" in text:
                missing.append(f"{path.name}:{node.lineno} (reads cfg)")
        assert not missing, (
            "make_destroy_ctx called without `create_fn` where the manifest "
            "is available; the destructor will not be derived from the "
            "declared creator on this path: " + "; ".join(missing)
        )

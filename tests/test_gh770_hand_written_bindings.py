"""gh-770 — a hand-written binding must survive a regeneration.

``native/src/<mod>/<mod>_ext_<obj>.c`` opens with "Hand-patches to this file
are preserved across jm commands". Three things made that untrue at once, and
each one is independently sufficient to lose the code:

1. :func:`_object._restore_c_function_bodies` only ever *replaces* a body whose
   name the fresh render also has. A hand-**added** function has no
   counterpart, so it was written away — in jm's own style, on any
   ``jm method`` / ``jm property`` against a module object.
2. :func:`_object._extract_c_function_bodies` anchored on ``name(``. GNU style
   (``SpaceBeforeParens: Always``, i.e. what every downstream running
   clang-format over jm's C ends up with) writes ``name (``, so extraction
   returned ``{}`` — and an empty extraction is not inert: the caller then has
   nothing to preserve and overwrites the whole file.
3. The same-signature guard compared whitespace-collapsed text, so a
   GNU-formatted ``Fir_step (…)`` never matched a freshly rendered
   ``Fir_step(…)`` and even a hand-**edited** body was skipped.

The matrix at the bottom is the shape of the bug: of the four
{style} x {edit kind} cells, only one behaved.
"""

import subprocess
import sys

import pytest

from just_makeit import _config as C
from just_makeit import _docsync as D
from just_makeit import _object as O

HAND_FN = """
static PyObject *
Fir_hand_added(FirObject *self, PyObject *args)
{
    /* HAND-ADDED: exists nowhere else. */
    Py_RETURN_NONE;
}
"""
HAND_ROW = (
    '  {"hand_added", (PyCFunction)Fir_hand_added, METH_VARARGS,\n'
    '   "hand_added() -> None\\n"},\n'
)
EDIT_MARK = "/* HAND-EDITED BODY */"


def _gnu(text: str) -> str:
    """*text* in the shape GNU-style clang-format leaves it — specifically
    the one token that mattered, a space between the name and the ``(``.

    Applied textually rather than by shelling out to clang-format: the test
    must fail for the recorded reason on a machine with no clang-format, and
    must not change meaning when a new clang-format release reflows something
    unrelated.
    """
    import re

    return re.sub(r"^(\w+) ?\(", r"\1 (", text, flags=re.M)


@pytest.fixture
def project(tmp_path):
    """A module project with one object, and its fragment path."""
    root = tmp_path / "proj"
    from just_makeit._apply import run as apply_run
    from just_makeit._module import run as module_run
    from just_makeit._new import run as new_run
    from just_makeit._object import run as object_run

    new_run("proj", root, fragments=True)
    module_run(root, "filter")
    object_run(root, "fir", "filter", state_vars=[("gain", "double", "1.0")])
    apply_run(root)
    frag = root / "native" / "src" / "filter" / "filter_ext_fir.c"
    assert frag.is_file()
    return root, frag


def _regen(root):
    cfg = C.load(root)
    O._regenerate_module_now(root, cfg, "filter", C.project_name(cfg))


def _plant_added(frag, gnu):
    text = frag.read_text()
    anchor = "static PyMethodDef Fir_methods[] = {"
    assert anchor in text
    text = text.replace(anchor, HAND_FN + "\n" + anchor, 1)
    text = text.replace(anchor, anchor + "\n" + HAND_ROW, 1)
    frag.write_text(_gnu(text) if gnu else text)


def _plant_edited(frag, gnu):
    text = frag.read_text()
    anchor = "Fir_step(FirObject *self, PyObject *args)\n{\n"
    assert anchor in text, "probe function missing from the render"
    text = text.replace(anchor, anchor + f"    {EDIT_MARK}\n", 1)
    frag.write_text(_gnu(text) if gnu else text)


class TestTheMatrix:
    """{jm style, GNU style} x {hand-added, hand-edited}."""

    @pytest.mark.parametrize("gnu", [False, True], ids=["jm", "gnu"])
    def test_a_hand_added_function_survives(self, project, gnu):
        root, frag = project
        _plant_added(frag, gnu)
        _regen(root)
        assert "Fir_hand_added" in frag.read_text()

    @pytest.mark.parametrize("gnu", [False, True], ids=["jm", "gnu"])
    def test_its_method_row_survives_too(self, project, gnu):
        """A function with no row is dead code; a row with no function is a
        link error. They only count as preserved together."""
        root, frag = project
        _plant_added(frag, gnu)
        _regen(root)
        text = frag.read_text()
        assert "Fir_hand_added" in text
        assert '"hand_added"' in text

    @pytest.mark.parametrize("gnu", [False, True], ids=["jm", "gnu"])
    def test_a_hand_edited_body_survives(self, project, gnu):
        root, frag = project
        _plant_edited(frag, gnu)
        _regen(root)
        assert EDIT_MARK in frag.read_text()

    def test_the_probe_is_not_an_always_regenerated_function(self, project):
        """Guard. ``_dealloc``/``_init`` are in ``_INFRA_SUFFIXES`` and are
        regenerated *by design*, so an edit planted in one is destroyed no
        matter what this fix does — a first draft of this test used
        ``Fir_dealloc`` and reported all four cells red, which looked like
        evidence and was an artifact."""
        assert not "Fir_step".endswith(("_dealloc", "_init"))


class TestTheParser:
    """The extraction is what everything else is built on."""

    def test_a_space_before_the_paren_still_parses(self):
        src = "static PyObject *\nFoo_bar (Obj *self)\n{\n  return NULL;\n}\n"
        assert "Foo_bar" in O._extract_c_function_bodies(src)

    def test_no_space_still_parses(self):
        src = "static PyObject *\nFoo_bar(Obj *self)\n{\n  return NULL;\n}\n"
        assert "Foo_bar" in O._extract_c_function_bodies(src)

    def test_a_newline_before_the_paren_does_not(self):
        """Only spaces and tabs. A newline there would let the brace scan
        walk into an unrelated construct."""
        src = "static PyObject *\nFoo_bar\n(Obj *self)\n{\n  return NULL;\n}\n"
        assert "Foo_bar" not in O._extract_c_function_bodies(src)

    def test_an_empty_extraction_is_what_made_it_silent(self, project):
        """The reason this was invisible: extraction failing returns ``{}``,
        which reads exactly like "this file has nothing to preserve"."""
        root, frag = project
        gnu_text = _gnu(frag.read_text())
        assert O._extract_c_function_bodies(gnu_text), (
            "GNU-formatted fragment must not extract to an empty dict — "
            "that is the failure mode, not the absence of one"
        )


class TestTheCarry:
    """:func:`_docsync.transplant_hand_written` in isolation."""

    def test_a_generated_function_is_not_duplicated(self, project):
        root, frag = project
        existing = frag.read_text()
        out = D.transplant_hand_written(existing, existing)
        assert out.count("Fir_step(FirObject") == existing.count(
            "Fir_step(FirObject"
        )

    def test_the_comment_above_a_hand_function_travels_with_it(self, project):
        root, frag = project
        _plant_added(frag, False)
        _regen(root)
        assert "HAND-ADDED: exists nowhere else." in frag.read_text()

    def test_it_is_idempotent(self, project):
        """A second regeneration must not stack a second copy."""
        root, frag = project
        _plant_added(frag, False)
        _regen(root)
        once = frag.read_text()
        _regen(root)
        twice = frag.read_text()
        assert once.count("Fir_hand_added") == twice.count("Fir_hand_added")
        assert once.count('"hand_added"') == twice.count('"hand_added"')


class TestRemovalStillRemoves:
    """The mirror-image failure, and CI is what caught it.

    Carrying a binding the fresh render lacks is right for a hand-written
    member and wrong for one `jm remove` just deleted — the two look
    identical from inside the transplant. I reasoned this was acceptable
    ("remove already leaves the `_core.c` body for you to delete by hand")
    and shipped it; the `jm_remove` example went red on three platforms.
    The member name now travels with the regeneration.
    """

    def test_a_removed_method_does_not_come_back(self, project):
        root, frag = project
        from just_makeit._method import run as method_run
        from just_makeit._remove import run as remove_run

        method_run(
            root,
            "fir",
            "tune",
            "filter",
            "float _Complex[]",
            "size_t",
            True,
            [],
        )
        assert '"tune"' in frag.read_text()

        remove_run(root, "method", "tune", object_name="fir", force=True)
        text = frag.read_text()
        assert '"tune"' not in text
        assert "Fir_tune" not in text

    def test_the_satellite_max_out_goes_with_it(self, project):
        """`tune` takes `tune_max_out`, which is a separate row bound to a
        separate wrapper and would otherwise be carried on its own."""
        root, frag = project
        from just_makeit._method import run as method_run
        from just_makeit._remove import run as remove_run

        method_run(
            root,
            "fir",
            "tune",
            "filter",
            "float _Complex[]",
            "size_t",
            True,
            [],
        )
        remove_run(root, "method", "tune", object_name="fir", force=True)
        assert "tune_max_out" not in frag.read_text()

    def test_a_hand_added_member_is_not_collateral(self, project):
        """Removing one member must not take an unrelated hand-written one
        with it — the drop set is a name, not a licence to stop carrying."""
        root, frag = project
        from just_makeit._method import run as method_run
        from just_makeit._remove import run as remove_run

        method_run(
            root,
            "fir",
            "tune",
            "filter",
            "float _Complex[]",
            "size_t",
            True,
            [],
        )
        _plant_added(frag, False)
        remove_run(root, "method", "tune", object_name="fir", force=True)
        text = frag.read_text()
        assert '"tune"' not in text
        assert "Fir_hand_added" in text


@pytest.mark.skipif(
    not __import__("shutil").which("cmake"), reason="needs a C toolchain"
)
class TestItCompiles:
    """Preserved-but-unwired is the failure this exists to rule out."""

    def test_the_carried_binding_builds(self, project):
        root, frag = project
        _plant_added(frag, True)
        _regen(root)
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys;from just_makeit._cli import main;"
                "sys.argv=['just-makeit','build'];main()",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=900,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        # Guard: a build that short-circuits also returns 0. The extension
        # has to exist, or this test passes without compiling anything.
        so = list((root / "src" / "proj" / "filter").glob("filter*.so"))
        assert so, f"no extension built: {proc.stdout[-2000:]}"

    def test_the_carried_binding_is_callable(self, project):
        """The end of the chain: preserved, wired, linked, and reachable
        from Python. Everything above this can pass on a file that never
        gets compiled."""
        root, frag = project
        _plant_added(frag, True)
        _regen(root)
        build = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys;from just_makeit._cli import main;"
                "sys.argv=['just-makeit','build'];main()",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=900,
        )
        assert build.returncode == 0, build.stdout + build.stderr
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "from proj.filter import Fir;"
                "assert Fir(gain=1.0).hand_added() is None;"
                "print('called')",
            ],
            cwd=root / "src",
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "called" in proc.stdout

"""gh-1160: a view's class docstring derives from its own `create_fn`.

A `[[<obj>.views]]` entry declares a second Python class over one C core, with
its **own** `create_fn`. gh-624 makes an authored `@brief` and `@code` on a
constructor become a class docstring and an `Examples` section on both faces --
for an object. A view got neither.

Measured on a scaffolded project, both faces were generic, differently:

    .pyi    docstring 'Deinterleaver component.'  <- generic seed
    tp_doc  "Deinterleaver type.\\n"               <- a DIFFERENT generic seed

so this was **two** bugs, one per face, and the issue's report of a rich stub
beside a five-word runtime understated it.

* **Runtime.** `_object._make_view_ctx` built `tp_doc` as
  `view.get("doc") or "<Component> type."` -- the manifest value if there was
  one, a placeholder otherwise, and the header never consulted. It now uses
  `_stubs.class_runtime_doc` with the view's `create_fn`, which is the same
  call `_glue.component_ctx` makes for the parent, so the two faces are one
  text with the stub-only parts stripped rather than two generators agreeing.

* **Stub.** `_stubs`' view overlay set `class_name` and, since gh-648, `doc` --
  but never `create_fn`. `_obj_stub` resolves the constructor through
  `C.object_create_fn(cfg_v, synth)`, which reads that key and otherwise falls
  back to `<synth>_create`, a synthetic name no header declares. So every
  lookup for the view's CLASS docstring missed, even though `_doc_blocks`
  already carried the real block under the real name. That is exactly the
  gh-685 bug one member up: gh-685 fixed the same miss for a view's inherited
  METHODS and left the constructor -- the one member a view does not inherit.

Note what is NOT claimed here: neither face carries the doxygen's extended
prose (the paragraphs after `@brief`). Checked against the parent object in
the same tree, which drops it too -- so the view is at parity, and carrying it
would be a separate change to both.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"

VIEW_DOXYGEN = (
    "/**\n"
    " * @brief VIEW_BRIEF_MARKER decodes a burst.\n"
    " *\n"
    " * @code\n"
    " * >>> VIEW_EXAMPLE_MARKER\n"
    " * @endcode\n"
    " */\n"
)


def _cli(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from just_makeit._cli import main; main()",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"},
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A module with an object and a view whose `create_fn` carries doxygen."""
    assert _cli("new", "pp", cwd=tmp_path).returncode == 0
    root = tmp_path / "pp"
    assert _cli("module", "coding", cwd=root).returncode == 0
    assert (
        _cli(
            "object",
            "inter",
            "--module",
            "coding",
            "--state",
            "gain:double:1.0",
            cwd=root,
        ).returncode
        == 0
    )
    assert (
        _cli(
            "view",
            "inter",
            "Deinterleaver",
            "--module",
            "coding",
            "--create-fn",
            "inter_create_rx",
            cwd=root,
        ).returncode
        == 0
    )
    h = root / "native" / "inc" / "inter" / "inter_core.h"
    body = h.read_text(encoding="utf-8")
    decl = re.search(r"^.*inter_create_rx.*$", body, re.M)
    assert decl, body
    h.write_text(
        body.replace(decl.group(0), VIEW_DOXYGEN + decl.group(0), 1), "utf-8"
    )
    assert _cli("apply", cwd=root).returncode == 0
    return root


def _stub(root: Path) -> str:
    return (root / "src" / "pp" / "coding" / "coding.pyi").read_text("utf-8")


def _runtime(root: Path) -> str:
    return (
        root / "native" / "src" / "coding" / "coding_ext_deinterleaver.c"
    ).read_text(encoding="utf-8")


def _tp_doc(root: Path) -> str:
    """The `.tp_doc` initialiser text, up to its terminating comma."""
    src = _runtime(root)
    i = src.index(".tp_doc")
    return src[i : src.index("\n    .tp_", i + 1)]


class TestBothFacesDerive:
    def test_the_runtime_doc_comes_from_the_views_create_fn(
        self, project: Path
    ) -> None:
        """The reported half: `.tp_doc = "Deinterleaver type.\\n"`."""
        doc = _tp_doc(project)
        assert "VIEW_BRIEF_MARKER decodes a burst." in doc, doc
        assert "Deinterleaver type." not in doc, doc

    def test_the_stub_doc_comes_from_the_views_create_fn(
        self, project: Path
    ) -> None:
        """The half the report did not mention, and which was equally broken:
        the overlay never carried `create_fn`."""
        cls = _stub(project)
        cls = cls[cls.index("class Deinterleaver") :]
        assert "VIEW_BRIEF_MARKER decodes a burst." in cls, cls[:400]
        assert "Deinterleaver component." not in cls[:200], cls[:400]

    def test_the_code_block_becomes_an_examples_section_on_both(
        self, project: Path
    ) -> None:
        """gh-624 already does this work for an object; the view uses the same
        machinery one level down, so it comes along."""
        cls = _stub(project)
        cls = cls[cls.index("class Deinterleaver") :]
        for face in (cls, _tp_doc(project)):
            assert "Examples" in face
            assert "VIEW_EXAMPLE_MARKER" in face

    def test_the_two_faces_agree(self, project: Path) -> None:
        """gh-642's property, which is the whole point: one text, not two
        generators that happen to match today."""
        cls = _stub(project)
        cls = cls[cls.index("class Deinterleaver") :]
        cls = cls[cls.index('"""') + 3 :]
        cls_body = cls[: cls.index('"""')]
        stub_lines = [ln.strip() for ln in cls_body.splitlines() if ln.strip()]
        rt = _tp_doc(project)
        for line in stub_lines:
            assert line in rt, f"stub line {line!r} missing from tp_doc:\n{rt}"


class TestPrecedenceAndFallback:
    def test_a_manifest_doc_outranks_the_header(self, tmp_path: Path) -> None:
        """Same precedence as an object: manifest `doc` > header `@brief`.
        gh-648 gave the view's `doc=` its class docstring; that must survive.

        Built fresh with the `doc` present before the FIRST apply, rather than
        added to the `project` fixture afterwards. That is not a convenience:
        `apply` replays the manifest through a temp tree reconstructed from
        CLI history, and no command carries an object or view `doc`, so a doc
        added after the tree exists does not reach the module `.pyi` at all
        (gh-1172). Writing it the other way would test that bug instead of
        this precedence.
        """
        assert _cli("new", "rr", cwd=tmp_path).returncode == 0
        root = tmp_path / "rr"
        assert _cli("module", "m", cwd=root).returncode == 0
        assert (
            _cli(
                "object",
                "o",
                "--module",
                "m",
                "--state",
                "g:double:1.0",
                cwd=root,
            ).returncode
            == 0
        )
        assert (
            _cli(
                "view",
                "o",
                "Vv",
                "--module",
                "m",
                "--create-fn",
                "o_create_alt",
                cwd=root,
            ).returncode
            == 0
        )
        p = root / "objects" / "o.toml"
        p.write_text(
            p.read_text(encoding="utf-8").replace(
                'class_name = "Vv"',
                'class_name = "Vv"\ndoc = "MANIFEST_VIEW_DOC wins."',
                1,
            ),
            encoding="utf-8",
        )
        h = root / "native" / "inc" / "o" / "o_core.h"
        body = h.read_text(encoding="utf-8")
        decl = re.search(r"^.*o_create_alt.*$", body, re.M)
        assert decl, body
        h.write_text(
            body.replace(
                decl.group(0),
                "/**\n * @brief VIEW_BRIEF_MARKER decodes a burst.\n */\n"
                + decl.group(0),
                1,
            ),
            encoding="utf-8",
        )
        assert _cli("apply", cwd=root).returncode == 0

        stub = (root / "src" / "rr" / "m" / "m.pyi").read_text("utf-8")
        stub = stub[stub.index("class Vv") :][:400]
        runtime = (root / "native" / "src" / "m" / "m_ext_vv.c").read_text(
            encoding="utf-8"
        )
        i = runtime.index(".tp_doc")
        runtime = runtime[i : runtime.index("\n    .tp_", i + 1)]
        for face in (stub, runtime):
            assert "MANIFEST_VIEW_DOC wins." in face, face
            assert "VIEW_BRIEF_MARKER" not in face, face

    def test_an_undocumented_view_keeps_the_BARE_placeholder(
        self, tmp_path: Path
    ) -> None:
        """Not just "unchanged" -- the exact bare form, and that matters.

        `class_runtime_doc` ALWAYS returns a block (generic summary plus the
        generated Parameters/Examples), so deriving unconditionally would
        replace `"Vv type."` with a full block for every undocumented view.
        `_docsync` refreshes a view's `tp_doc` only while
        `_is_generic_tp_doc` recognises it, and that predicate knows the bare
        form -- so a full-block fallback would make every undocumented view's
        docstring unreclaimable from then on. That is a worse bug than the one
        being fixed and it only surfaces on a LATER apply, which is why this
        asserts the literal rather than "not the derived text".
        """
        assert _cli("new", "qq", cwd=tmp_path).returncode == 0
        root = tmp_path / "qq"
        assert _cli("module", "m", cwd=root).returncode == 0
        assert (
            _cli(
                "object",
                "o",
                "--module",
                "m",
                "--state",
                "g:double:1.0",
                cwd=root,
            ).returncode
            == 0
        )
        assert (
            _cli(
                "view",
                "o",
                "Vv",
                "--module",
                "m",
                "--create-fn",
                "o_create_alt",
                cwd=root,
            ).returncode
            == 0
        )
        assert _cli("apply", cwd=root).returncode == 0
        rt = (root / "native" / "src" / "m" / "m_ext_vv.c").read_text("utf-8")
        assert '.tp_doc       = "Vv type.\\n",' in rt, (
            "the bare fallback changed shape; `_docsync._is_generic_tp_doc` "
            "will no longer reclaim it, so this view's docstring is now "
            "frozen forever:\n"
            + rt[rt.index(".tp_doc") : rt.index(".tp_doc") + 300]
        )

        # ...and it must still be reclaimable, which is the property the
        # literal above is standing in for. Assert it directly too.
        from just_makeit import _docsync

        assert _docsync._is_generic_tp_doc('"Vv type.\\n"')


class TestItStaysConsistent:
    def test_status_check_is_clean_and_apply_is_idempotent(
        self, project: Path
    ) -> None:
        out = _cli("status", "--check", cwd=project)
        assert out.returncode == 0, out.stdout
        before = (_stub(project), _runtime(project))
        assert _cli("apply", cwd=project).returncode == 0
        assert (_stub(project), _runtime(project)) == before

"""gh-1113: a handle method's runtime `__doc__`.

Every author-declared method registered with a `NULL` `ml_doc`, so
`help(Sink.drain)` was empty on **every** handle module — while the `.pyi`
beside it carried the vendored header's full numpy prose, derived in the same
render pass from the same block. `close`, jm's own plumbing, was the only
member with a docstring.

`render_ext` did not take `doc_blocks` at all, so this was not a block being
dropped: the prose never reached the C.

The face that went missing is the one a person reads. A `.pyi` serves the type
checker and the IDE hover; `help()` at the REPL is what someone does with no
IDE open, in a notebook, or debugging a wheel.

Two things are gated here, and the second is why this file compiles anything:

- the text matches the stub's, because both come from `_numpy_sections` via
    `render_runtime_doc` / `render_numpy_doc`, and the shape both document
    comes from `py_face` — one chain, after gh-1116 and gh-1118 each cost a
    defect to a second copy of it;
- the emitted C **builds and imports**. `ml_doc` is a C string literal spliced
    from author prose; a stray quote or newline there is an unterminated
    literal, which is a compile error in a user's project and invisible to
    every text assertion.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _handle

_CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
_LINK = (
    ["-bundle", "-undefined", "dynamic_lookup"]
    if sys.platform == "darwin"
    else ["-shared"]
)

_DOC = {
    "b_drain": (
        "/**\n"
        " * @brief Flush pending records to the sink.\n"
        " *\n"
        " * Blocks until the sink has accepted everything queued.\n"
        " * @param timeout_ms  How long to wait, in ms.\n"
        " */"
    )
}


def _cfg(**over):
    m = {
        "name": "drain",
        "fn": "b_drain",
        "returns": "int",
        "error": "OSError",
        "error_message": "budget ran out",
        "args": [{"name": "timeout_ms", "type": "int", "default": "0"}],
    }
    m.update(over)
    return {
        "project": {"name": "p"},
        "module": {
            "sink": {
                "kind": "handle",
                "backing": "b",
                "header": "b/b.h",
                "type_name": "Sink",
                "close_fn": "b_close",
                "create_fn": "b_open",
                # One create_arg on purpose: a handle with none
                # emits `kwlist[] = {, NULL}` and does not
                # compile (gh-1131, filed separately). Using
                # that shape here would fail this gate for a
                # reason that has nothing to do with docstrings.
                "create_args": [{"name": "path", "type": "path"}],
                "methods": [m],
            }
        },
    }


class TestTheProseReachesTheBinding:
    def test_the_header_brief_is_in_the_method_table(self):
        ext = _handle.render_ext(_cfg(), "sink", _DOC)
        assert "Flush pending records to the sink." in ext

    def test_no_method_registers_a_NULL_doc(self):
        """The reported symptom, stated directly."""
        ext = _handle.render_ext(_cfg(), "sink", _DOC)
        assert (
            "(PyCFunction)Sink_drain, METH_VARARGS | METH_KEYWORDS, NULL"
            not in ext
        )

    def test_a_method_with_nothing_derivable_still_gets_its_signature(self):
        """Strictly more than NULL, and what `close` has always had.

        No header block AND no declared raise — with either one, the method
        earns the full numpy form instead, which is the point of the feature
        rather than a fallback.
        """
        cfg = _cfg()
        cfg["module"]["sink"]["methods"][0].pop("error")
        cfg["module"]["sink"]["methods"][0].pop("error_message")
        ext = _handle.render_ext(cfg, "sink")
        assert "drain(timeout_ms=0) -> int" in ext

    def test_the_declared_raise_is_documented_at_runtime_too(self):
        """gh-1111 gave the stub a `Raises` section; the REPL face is where a
        caller is most likely to be looking for it."""
        ext = _handle.render_ext(_cfg(), "sink", _DOC)
        assert "Raises" in ext and "OSError" in ext
        assert "budget ran out" in ext

    def test_a_manifest_doc_outranks_the_header(self):
        ext = _handle.render_ext(_cfg(doc="Manifest wins."), "sink", _DOC)
        assert "Manifest wins." in ext

    def test_the_two_faces_agree(self):
        """The stub's text is the runtime text plus indent and quotes."""
        cfg = _cfg()
        ext = _handle.render_ext(cfg, "sink", _DOC)
        pyi = _handle.render_pyi(cfg, "sink", _DOC)
        for line in (
            "Flush pending records to the sink.",
            "timeout_ms : int",
            "How long to wait, in ms.",
        ):
            assert line in pyi, line
            assert line in ext, line


@pytest.mark.skipif(_CC is None, reason="no C compiler available")
def test_the_emitted_doc_compiles_and_help_shows_it(tmp_path):
    """`ml_doc` is a C string literal built from AUTHOR prose.

    A quote or newline that escapes the literal is an unterminated string —
    a compile error in a user's project, and one no text assertion can see.
    So the prose here carries both.
    """
    quoted = {
        "b_drain": (
            "/**\n"
            ' * @brief Flush the sink\'s "pending" queue.\n'
            " *\n"
            ' * A backslash \\\\ and a quote " both survive.\n'
            " */"
        )
    }
    ext = _handle.render_ext(_cfg(args=[]), "sink", quoted)
    # Strip the parts that need the real backing library; keep the table.
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "b.h").write_text(
        "#ifndef B_H\n#define B_H\ntypedef struct b b_t;\n"
        "b_t *b_open(const char *path);\nvoid b_close(b_t *);\n"
        "int b_drain(b_t *);\n#endif\n"
    )
    (tmp_path / "b.c").write_text(
        '#include "b/b.h"\n#include <stdlib.h>\n'
        "struct b { int x; };\n"
        "b_t *b_open(const char *path) { (void)path; return calloc(1, sizeof(struct b)); }\n"
        "void b_close(b_t *p) { free(p); }\n"
        "int b_drain(b_t *p) { (void)p; return 0; }\n"
    )
    src = tmp_path / "sink.c"
    src.write_text(ext)
    pkg = tmp_path / "p"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    import numpy as np

    subprocess.run(
        [
            _CC,
            *_LINK,
            "-fPIC",
            f"-I{tmp_path}",
            f"-I{sysconfig.get_paths()['include']}",
            f"-I{np.get_include()}",
            str(src),
            str(tmp_path / "b.c"),
            "-o",
            str(pkg / f"sink{sysconfig.get_config_var('EXT_SUFFIX')}"),
        ],
        check=True,
        capture_output=True,
    )
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, '.')\n"
            "from p.sink import Sink\n"
            "print(Sink.drain.__doc__ or '<EMPTY>')",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert 'Flush the sink\'s "pending" queue.' in r.stdout, r.stdout
    assert "<EMPTY>" not in r.stdout


@pytest.mark.parametrize(
    "case",
    ["prose", "raises_only", "override", "nothing"],
)
def test_the_two_faces_never_disagree(case):
    """Whatever one face says, the other says — modulo indent and quotes.

    The interesting case is `nothing`. `render_runtime_doc` returns a
    SKELETON there (summary, `Parameters` with `Input.`, `Returns` with
    `Output.`) while the stub collapses to its one-line signature — so
    emitting unconditionally made the two faces differ for exactly the
    methods that have nothing to say. Caught by writing this test, not by
    reading the renderer.
    """
    cfg = _cfg()
    m = cfg["module"]["sink"]["methods"][0]
    blocks = {}
    if case == "prose":
        blocks = _DOC
    elif case == "override":
        m["doc"] = "Declared in the manifest."
    if case in ("prose", "override", "nothing"):
        m.pop("error", None)
        m.pop("error_message", None)
    ext = _handle.render_ext(cfg, "sink", blocks)
    pyi = _handle.render_pyi(cfg, "sink", blocks)

    body = ext[ext.index("static PyMethodDef") :]
    body = body[: body.index("{NULL, NULL, 0, NULL}")]
    # Parse the C string literals rather than hand-stripping punctuation:
    # the first version of this test left a trailing `"}` on the last line
    # and failed on its own quoting, not on any disagreement.
    runtime = []
    for lit in re.findall(r'"((?:[^"\\]|\\.)*)"', body):
        runtime += (
            lit.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
        ).split("\n")

    for line in runtime:
        if not line.strip():
            continue
        assert line.strip() in pyi, (
            f"{case}: runtime says {line.strip()!r}, the stub does not"
        )

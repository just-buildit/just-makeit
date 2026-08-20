"""gh-1039: a ``single = true`` method's runtime doc, and the class it is in.

gh-642 established the invariant that the runtime ``PyMethodDef`` doc **is**
the ``.pyi`` docstring with the indent and delimiters removed, and routed four
method shapes through the shared renderer. It missed a fifth. A record method
declared ``single = true`` kept a canned signature line as its *entire*
runtime doc::

    "find(x, max_errors) -> Hit record (found, offset)."

while its stub carried the full authored block — brief, body, ``Parameters``,
``Returns``, ``Examples``. Found downstream in doppler#900, on one object where
the only difference between the thin method and the full one was that one
returned a record.

There was no authoring move that fixed it. A manifest ``doc`` reached the stub
and not the C literal, because the literal above never consulted either source.

Two layers, matching ``test_gh642_runtime_doc_parity``:

1. :class:`TestSingleRecordParity` proves the fix end to end — the invariant
   holds for this shape through the generators.
2. :class:`TestNoCannedDocLiterals` is the reason a sixth cannot arrive the
   same way. gh-642 fixed four shapes as four instances and nothing afterwards
   asked whether a *new* shape had joined them, so this one sat undetected
   until a downstream binding surfaced it. The ratchet derives the answer from
   the source on every run: no fixture, no list of shapes to keep current.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_METHODS_PY = (
    Path(__file__).parent.parent
    / "src"
    / "just_makeit"
    / "_context"
    / "_methods.py"
)

# The authored block, in the shape the issue reports: every section populated,
# so any one of them going missing from the runtime face is visible.
_AUTHORED = """ * @brief Locate the sync word in a block of bits.
 *
 * Walks every offset once, so cost is O(len(bits) * len(marker)).
 *
 * @param in Input bits. Any length, including zero.
 * @param max_errors Tolerance, in bit errors.
 * @return The best hit found, or a miss.
 *
 * @code
 * >>> hit = obj.find(bits, 2)
 * >>> hit.found
 * 1
 * @endcode"""

_C_DOC_LINE = re.compile(r'^\s*"(.*)"[,}\s]*$')


def _runtime_doc(ext_c: str, method: str) -> list[str]:
    """The ``PyMethodDef`` doc literal for *method*, unescaped to lines."""
    start = ext_c.index(f'{{"{method}",')
    out: list[str] = []
    for raw in ext_c[start:].splitlines()[1:]:
        m = _C_DOC_LINE.match(raw)
        if not m:
            break
        out.append(m.group(1))
    assert out, f"no doc literal found for {method}()"
    return "".join(out).encode().decode("unicode_escape").split("\n")


def _stub_doc(pyi: str, method: str) -> list[str]:
    """The ``.pyi`` docstring for *method*, dedented and undelimited."""
    m = re.search(
        rf'    def {method}\([^\n]*\n        """(.*?)\n        """',
        pyi,
        re.S,
    )
    assert m, f"no stub docstring found for {method}()"
    body = m.group(1).split("\n")
    return [body[0]] + [
        ln[8:] if ln.startswith("        ") else ln for ln in body[1:]
    ]


_FIELDS = [
    {"name": "found", "type": "int", "doc": "Nonzero on a hit."},
    {"name": "offset", "type": "size_t", "doc": "Bit offset."},
]


def _scaffold(tmp_path: Path, **method_kw) -> Path:
    """A project with one ``single = true`` method and nothing else."""
    root = tmp_path / "demo"
    new_run("demo", root)
    object_run(
        root,
        "sync",
        None,
        state_vars=[("marker", "uint64_t", "0")],
        arg_type="void",
        return_type="float",
    )
    method_run(
        root,
        "sync",
        "find",
        None,
        "float[]",
        "sync_hit_t",
        False,
        [],
        params=method_kw.pop("params", [("max_errors", "int")]),
        single=True,
        record_name="SyncHit",
        result_fields=method_kw.pop(
            "result_fields", [dict(f) for f in _FIELDS]
        ),
        **method_kw,
    )
    return root


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A scaffold whose ``single = true`` find() carries a full block."""
    root = _scaffold(tmp_path)
    header = root / "native" / "inc" / "sync" / "sync_core.h"
    text = header.read_text(encoding="utf-8")
    assert " * @brief find." in text, "the scaffold no longer seeds @brief"
    header.write_text(
        text.replace(" * @brief find.", _AUTHORED, 1), encoding="utf-8"
    )
    apply_run(root)
    return root


class TestSingleRecordParity:
    """The gh-642 invariant, on the shape gh-642 missed."""

    def test_runtime_block_matches_the_stub(self, project):
        ext_c = (project / "native/src/sync/sync_ext.c").read_text()
        pyi = (project / "src/demo/sync.pyi").read_text()
        runtime = _runtime_doc(ext_c, "find")
        stub = _stub_doc(pyi, "find")
        # The runtime literal leads with a signature line the stub does not
        # need (the stub has a real `def`), and appends jm's synthesized
        # doctest, which the stub also carries -- so only the lead differs.
        assert runtime[0].startswith("find("), runtime[0]
        body = [ln for ln in runtime[2:] if ln.strip()]
        assert body == [ln for ln in stub if ln.strip()]

    def test_authored_example_replaces_the_synthesised_demo(self, project):
        """One Examples section, not the author's plus jm's placeholder."""
        ext_c = (project / "native/src/sync/sync_ext.c").read_text()
        runtime = "\n".join(_runtime_doc(ext_c, "find"))
        assert ">>> hit = obj.find(bits, 2)" in runtime
        assert runtime.count("Examples") == 1
        assert ">>> from demo import Sync" not in runtime

    def test_authored_prose_reaches_the_runtime_face(self, project):
        """The regression itself: the block in the .so, not just the stub."""
        ext_c = (project / "native/src/sync/sync_ext.c").read_text()
        runtime = "\n".join(_runtime_doc(ext_c, "find"))
        assert "Locate the sync word in a block of bits." in runtime
        assert "Walks every offset once" in runtime
        assert "Input bits. Any length, including zero." in runtime
        assert "Tolerance, in bit errors." in runtime
        assert "The best hit found, or a miss." in runtime

    def test_signature_line_still_names_the_record_fields(self, project):
        """A structseq prints as a bare tuple; the field list earns its width.

        The canned literal this replaced did carry the names, and dropping
        them to gain the block would have traded one omission for another.
        """
        ext_c = (project / "native/src/sync/sync_ext.c").read_text()
        head = _runtime_doc(ext_c, "find")[0]
        assert head == "find(x, max_errors) -> SyncHit record (found, offset)"

    def test_manifest_doc_reaches_the_runtime_face(self, tmp_path):
        """The other source the canned literal ignored.

        With no header prose at all, a manifest `doc` is the only summary
        there is -- and it reached the stub alone, which is why the issue
        reports that no authoring move fixed this.
        """
        root = _scaffold(
            tmp_path,
            params=[],
            result_fields=[{"name": "found", "type": "int"}],
            doc="Search the block for the marker.",
        )
        ext_c = (root / "native/src/sync/sync_ext.c").read_text()
        runtime = "\n".join(_runtime_doc(ext_c, "find"))
        assert "Search the block for the marker." in runtime

    def test_synthesised_demo_constructs_and_calls(self, tmp_path):
        """jm's fallback doctest, in the shared shape the peers emit.

        Its own scaffold: the fixture's header carries `@code`, which
        deliberately suppresses this fallback.
        """
        root = _scaffold(tmp_path)
        ext_c = (root / "native/src/sync/sync_ext.c").read_text()
        runtime = "\n".join(_runtime_doc(ext_c, "find"))
        assert ">>> from demo import Sync" in runtime
        assert ">>> rec = obj.find(" in runtime
        # The declared int param must render as an int, not as an array or a
        # bare name -- the demo is executable prose (gh-1021).
        assert (
            ">>> rec = obj.find(np.zeros(4, dtype=np.float32), 0)" in runtime
        )


class TestNoCannedDocLiterals:
    """A ratchet on the shape that let gh-1039 sit undetected.

    Every ``PyMethodDef`` doc must come from the shared renderer, reached
    either through ``_build_ml_doc`` directly, through a local already built
    by it, or by delegating the whole entry to a helper. A site that spells
    its doc as a literal in the ``pmd_lines.append`` call is a shape whose
    runtime face cannot carry the header, which is gh-1039 exactly.

    One such site remains, and it is a different feature: ``--varargs``, whose
    binding body is hand-written and whose *stub* is canned to match, so its
    two faces agree rather than disagree. It is filed as gh-1040. This count
    may only shrink.
    """

    CANNED_SITES = 1

    @staticmethod
    def _canned_sites() -> list[int]:
        """Line numbers of ``pmd_lines.append`` calls with a literal doc."""
        src = _METHODS_PY.read_text(encoding="utf-8")
        # Locals assigned from _build_ml_doc are derived too -- passing one
        # through a variable is not a canned literal.
        built = set(re.findall(r"^\s*(_\w+)\s*=\s*_build_ml_doc\(", src, re.M))
        out: list[int] = []
        for m in re.finditer(r"pmd_lines\.append\(", src):
            i, depth = m.end(), 1
            while depth:
                depth += (src[i] == "(") - (src[i] == ")")
                i += 1
            call = src[m.start() : i]
            arg = call[call.index("(") + 1 : -1].strip()
            derived = (
                "_build_ml_doc" in call
                # `pmd_lines.append(pmd)` -- the entry came from a helper.
                or re.fullmatch(r"_?\w+", arg)
                or any(v in call for v in built)
            )
            if not derived:
                out.append(src[: m.start()].count("\n") + 1)
        return out

    def test_ratchet_may_only_shrink(self):
        sites = self._canned_sites()
        assert len(sites) <= self.CANNED_SITES, (
            f"new PyMethodDef doc literal(s) at {_METHODS_PY.name} lines "
            f"{sites}: a doc spelled in the append call cannot carry the "
            f"header or the manifest `doc` -- route it through "
            f"_runtime_doc()/_build_ml_doc like every other shape (gh-1039)."
        )
        assert len(sites) == self.CANNED_SITES, (
            f"only {len(sites)} canned site(s) left, ratchet says "
            f"{self.CANNED_SITES} -- lower CANNED_SITES to lock the gain in."
        )

    def test_the_detector_sees_a_canned_literal(self):
        """The gate is not vacuous.

        A count of zero would pass ``<=`` against a detector that matches
        nothing, so the surviving site is also what proves the scan is armed.
        """
        assert self._canned_sites(), (
            "the detector found no canned site at all -- it has stopped "
            "matching, and the ratchet above is now green on anything"
        )

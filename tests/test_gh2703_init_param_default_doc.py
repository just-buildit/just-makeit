"""gh-2703 item 1: an init param's `default_raw` constant is named in its doc.

`default_raw = "<CONST>"` (gh-1099) is how a constructor parameter takes a
header constant as its default: the binding seeds it, the parameter is
optional, and the stub signature says ``...`` because Python cannot spell
C. The class docstring's ``Parameters`` entry, the one place that could say
what ``...`` is, said only ``w : int`` -- its clause was written for a
literal ``default`` and read nothing else -- on the ``.pyi`` and on the
runtime ``tp_doc`` derived from it (`_stubs.class_runtime_doc`).

It now reads ``w : int, default WIN_HANN``, through the clause a method
parameter's entry uses (gh-2679: `_context._types.doc_default`,
`_docstring.defaulted_ann`), and a literal ``default`` keeps its Python
spelling.

GATE: for a standalone object, a module object and a view, the class
      docstring on both faces names a `default_raw` constant and a literal
      default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _config as C

#: (producer, its .pyi, its runtime fragment, (literal param, constant
#: param, the constant)). Every name differs from every other producer's:
#: the module stub holds the module object's class AND the view's, and a
#: line one carried would otherwise pass for the other's.
_PRODUCERS = {
    "standalone": (
        "src/ic/a.pyi",
        "native/src/a/a_ext.c",
        ("n", "w", "WIN_A"),
    ),
    "module object": (
        "src/ic/dsp/dsp.pyi",
        "native/src/dsp/dsp_ext_fir.c",
        ("k", "x", "WIN_F"),
    ),
    "view": (
        "src/ic/dsp/dsp.pyi",
        "native/src/dsp/dsp_ext_fir2.c",
        ("m", "v", "WIN_V"),
    ),
}


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, f"{args}\n{r.stdout}\n{r.stderr}"


def _ip(lit: str, const: str) -> "list[str]":
    # The constant param is declared with a literal here and switched to
    # `default_raw` in the manifest below: `default_raw` has no CLI form.
    return ["--init-param", f"{lit}:int:3", "--init-param", f"{const}:int:0"]


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("gh2703")
    _ok("new", "ic", cwd=tmp)
    root = tmp / "ic"
    _, _, (lit, const, _c) = _PRODUCERS["standalone"]
    _ok("object", "a", *_ip(lit, const), cwd=root)
    _ok("module", "dsp", cwd=root)
    _, _, (lit, const, _c) = _PRODUCERS["module object"]
    _ok("object", "fir", "--module", "dsp", *_ip(lit, const), cwd=root)
    _, _, (lit, const, _c) = _PRODUCERS["view"]
    _ok(
        "view", "fir", "Fir2", "--module", "dsp",
        "--create-fn", "ic_fir_create_two", *_ip(lit, const),
        cwd=root,
    )  # fmt: skip
    cfg = C.load(root)
    rows = {
        "standalone": cfg["a"]["init_params"],
        "module object": cfg["fir"]["init_params"],
        "view": cfg["fir"]["views"][0]["init_params"],
    }
    for producer, (_s, _r, (_l, const, cname)) in _PRODUCERS.items():
        (row,) = [p for p in rows[producer] if p["name"] == const]
        del row["default"]
        row["default_raw"] = cname
    # An authored summary is what makes the runtime `tp_doc` the full class
    # docstring rather than its one-line placeholder, on every producer.
    cfg["a"]["doc"] = "An a."
    cfg["fir"]["doc"] = "A fir."
    cfg["fir"]["views"][0]["doc"] = "A second fir."
    C.save(root, cfg)
    _ok("apply", cwd=root)
    return root


_CASES = [
    (producer, face, line)
    for producer, (_s, _r, (lit, const, cname)) in _PRODUCERS.items()
    for face in ("stub", "runtime")
    for line in (f"{lit} : int, default 3", f"{const} : int, default {cname}")
]


@pytest.mark.parametrize(("producer", "face", "line"), _CASES)
def test_the_class_docstring_names_the_default(project, producer, face, line):
    stub, runtime, _ = _PRODUCERS[producer]
    if face == "stub":
        text = (project / stub).read_text(encoding="utf-8")
        assert f"\n    {line}\n" in text, f"{producer} stub: {line!r}"
    else:
        text = (project / runtime).read_text(encoding="utf-8")
        assert f'"{line}\\n"' in text, f"{producer} runtime: {line!r}"

"""gh-944: jm's shipped C headers must not declare reserved identifiers.

C reserves, for the implementation:

- any identifier beginning with an underscore followed by an uppercase letter
  or a second underscore, in **any** scope;
- any identifier beginning with an underscore, at file scope.

jm shipped 16 of them across three headers — `_JM_LIKELY_` and eight siblings
in jm_perf.h, `_jm_hsum256_f32`/`_f64` in jm_simd.h, `_jm_dcmp`/`_jm_quantile`
in jm_bench.h. Declaring one is undefined behaviour: the implementation is
free to define the same name as a macro, and then the project's own header
stops compiling for a reason that reads as a toolchain bug.

The gate walks the template tree rather than naming files, so a header added
later is covered without editing anything here — and so is a header added to a
directory that does not exist yet. The list of offenders lived in an issue
once; a list is what goes stale.

Only DEFINITIONS are checked, never uses: `__builtin_expect` and
`__attribute__` are the implementation's to spell, and jm calling them is
correct.

And the templates are scanned as they will be RENDERED, not as they sit on
disk. `/*<<component>>*/_dealloc` is a placeholder followed by a suffix — it
becomes `gain_dealloc`, which is nobody's reserved identifier. Scanning the
raw text reported seven of those in component_ext.c on this gate's first run.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

_TEMPLATES = Path(__file__).parent.parent / "src" / "just_makeit" / "templates"

#: `#define _FOO` / `#define _foo(x)` — a macro whose NAME is reserved.
_RESERVED_DEFINE = re.compile(r"^\s*#\s*define\s+(_\w+)", re.MULTILINE)

#: A file-scope declaration whose name is reserved: `static int _checks`,
#: `static inline float _jm_hsum(...)`, or a K&R-style definition where the
#: name opens the line. Deliberately narrow — a local `size_t _i` inside a
#: function body is legal C and is not matched.
_RESERVED_DECL = re.compile(
    r"^\s*(?:static|extern)\b[^;(){}]*?\b(_\w+)\s*[\(=;\[]"
    r"|^(_\w+)\s*\(",
    re.MULTILINE,
)


#: `/*<<token>>*/` — the placeholder form C templates use so clang-format can
#: parse them. Substituted with a plain identifier so the scan sees the name
#: the rendered file will actually carry.
_PLACEHOLDER = re.compile(r"/\*<<\w+>>\*/")


def _c_templates() -> list[Path]:
    return sorted(
        p
        for p in _TEMPLATES.rglob("*")
        if p.suffix in {".h", ".c"} and p.is_file()
    )


def _offenders(text: str) -> set[str]:
    text = _PLACEHOLDER.sub("R", text)
    names = set(_RESERVED_DEFINE.findall(text))
    for a, b in _RESERVED_DECL.findall(text):
        if a:
            names.add(a)
        if b:
            names.add(b)
    return names


def test_the_scan_is_armed():
    """A scan that finds nothing must prove it looked at something.

    Without this, deleting the template tree — or a suffix filter that stops
    matching — turns every assertion below green.
    """
    files = _c_templates()
    assert len(files) >= 10, f"only {len(files)} C templates found"
    assert any(p.name == "jm_perf.h" for p in files)
    # And the detector must be able to detect: feed it the shape it hunts.
    assert _offenders("#define _JM_LIKELY_(x) (x)") == {"_JM_LIKELY_"}
    assert _offenders("static inline int _jm_dcmp(const void *a)") == {
        "_jm_dcmp"
    }
    # ...while leaving alone: legitimate USES of implementation names, a
    # block-scope local (legal C), and a placeholder-prefixed name that
    # renders to something perfectly ordinary.
    assert not _offenders("#define JM_LIKELY(x) __builtin_expect(!!(x), 1)")
    assert not _offenders("    size_t _i = 0;")
    assert not _offenders("static void /*<<component>>*/_dealloc(PyObject *o)")


def test_no_shipped_c_template_declares_a_reserved_identifier():
    found = {}
    for p in _c_templates():
        names = _offenders(p.read_text(encoding="utf-8"))
        if names:
            found[str(p.relative_to(_TEMPLATES))] = sorted(names)
    assert not found, (
        "reserved identifiers declared in shipped C templates — the "
        "implementation may define these itself, and then the generated "
        f"project stops compiling: {found}"
    )

"""How jm resolves a header's quoted includes for struct member docs.

gh-2779: a C compiler resolves ``#include "x.h"`` beside the including
header first, then along ``-I``. jm resolved under ``native/inc`` only, so a
struct in a sibling header spelled ``#include "ccsds_415.h"`` (rather than
``"p/ccsds/ccsds_415.h"``) compiled and was never read: every record field
documented there rendered undocumented, with no message.

gh-2781: (1) a header's OWN definition of a struct beats an included one of
the same name -- a rule no test held, so reversing the merge stayed green;
(2) a quoted absolute include (``#include "/usr/include/x.h"``) was read, a
file outside the project. Only a header inside ``native/inc`` is followed.

Each case drives jm through ``run_cli`` -- ``new``, then ``apply`` of a
fragment, then the author's headers, then ``apply`` again -- and reads the
field docs off the ``.pyi`` jm wrote.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _jmrun import run_cli

FRAGMENT = """\
[module.m]
objects = []

[[module.m.functions]]
name = "scene"
return_type = "scene_t"
single = true
record_name = "Scene"
result_fields = [{ name = "a", type = "double" }]
"""


def _scene_h(doc: str) -> str:
    return (
        "#ifndef P_M_SCENE_H\n#define P_M_SCENE_H\n"
        f"typedef struct {{\n  double a; /**< {doc} */\n}} scene_t;\n"
        "#endif\n"
    )


def _stub_after(tmp_path: Path, write) -> str:
    """The ``.pyi`` once *write* has edited the scaffolded headers."""
    r = run_cli("new", "p", "--no-c-prefix", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    root = tmp_path / "p"
    frag = tmp_path / "frag.toml"
    frag.write_text(FRAGMENT, encoding="utf-8")
    r = run_cli("apply", str(frag), cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    inc = root / "native" / "inc" / "p" / "m"
    write(inc, inc / "m_core.h")
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    return (root / "src" / "p" / "m" / "m.pyi").read_text(encoding="utf-8")


def _add(header: Path, text: str) -> None:
    """Put *text* after the header's first include, as an author would."""
    src = header.read_text(encoding="utf-8")
    at = src.index("#include")
    header.write_text(src[:at] + text + src[at:], encoding="utf-8")


def test_a_sibling_include_is_read(tmp_path):
    """gh-2779: the spelling the compiler accepts, beside the header."""

    def write(inc, core_h):
        (inc / "scene.h").write_text(_scene_h("Sibling doc."), "utf-8")
        _add(core_h, '#include "scene.h"\n')

    assert "Sibling doc." in _stub_after(tmp_path, write)


def test_the_include_dir_spelling_still_reads(tmp_path):
    """The over-fix guard: ``"p/m/scene.h"`` is still found along -I."""

    def write(inc, core_h):
        (inc / "scene.h").write_text(_scene_h("Include-dir doc."), "utf-8")
        _add(core_h, '#include "p/m/scene.h"\n')

    assert "Include-dir doc." in _stub_after(tmp_path, write)


def test_a_sibling_of_an_included_header_is_read(tmp_path):
    """Transitively: each header's includes resolve beside THAT header."""

    def write(inc, core_h):
        sub = inc / "sub"
        sub.mkdir()
        (sub / "scene.h").write_text(_scene_h("Nested doc."), "utf-8")
        (sub / "all.h").write_text('#include "scene.h"\n', "utf-8")
        _add(core_h, '#include "sub/all.h"\n')

    assert "Nested doc." in _stub_after(tmp_path, write)


def test_the_headers_own_definition_wins(tmp_path):
    """gh-2781 (1): the nearest definition of a struct name documents it."""

    def write(inc, core_h):
        (inc / "scene.h").write_text(_scene_h("Included doc."), "utf-8")
        _add(core_h, '#include "p/m/scene.h"\n')
        src = core_h.read_text(encoding="utf-8")
        at = src.rindex("#endif")
        own = (
            "#if 0\ntypedef struct {\n  double a; /**< Own doc. */\n"
            "} scene_t;\n#endif\n"
        )
        core_h.write_text(src[:at] + own + src[at:], encoding="utf-8")

    stub = _stub_after(tmp_path, write)
    assert "Own doc." in stub, stub
    assert "Included doc." not in stub, stub


@pytest.mark.parametrize("how", ["absolute", "climbs-out"])
def test_a_header_outside_the_include_dir_is_not_read(tmp_path, how):
    """gh-2781 (2): only a project header under native/inc is followed."""
    outside = tmp_path / "outside" / "scene.h"
    outside.parent.mkdir()
    outside.write_text(_scene_h("Outside doc."), "utf-8")

    def write(inc, core_h):
        spelled = (
            str(outside)
            if how == "absolute"
            else Path("../../../../..", "outside", "scene.h").as_posix()
        )
        assert (core_h.parent / spelled).resolve() == outside.resolve()
        _add(core_h, f'#include "{spelled}"\n')

    assert "Outside doc." not in _stub_after(tmp_path, write)

"""gh-1208: a synthesized doctest must import the class from where it lands.

A module component's class lives at ``<pkg>/<module>/<module>.so`` and imports
as ``from <pkg>.<module> import <Component>``. Every synthesized example in the
**runtime** binding named ``from <pkg> import <Component>`` instead, so each
one raised ``ImportError`` the moment anything executed it -- while the
``.pyi`` for the same methods was right, because exactly one of the fourteen
call sites that build this line asked whether the component was in a module.

Two things about the checks below.

**The expected path is derived from the filesystem, not from the renderer.**
The stub for a component lands in the directory its class imports from -- that
is what makes ``src/commz/dsp/dsp.pyi`` importable as ``commz.dsp`` -- so the
directory IS the oracle. Asserting the rendered line against a second call of
the same helper that rendered it would pass against any consistent mistake,
including this one: the bug was never an inconsistency in the expression, it
was thirteen sites not asking a question.

**It walks every object in the manifest** rather than naming the ones the bug
was reported against, so a shape added later is covered without registering it
anywhere. The reported case was a flat module; the dotted one below is here
because ``module_paths`` splits a dotted id into three roles and only one of
them is the importable path -- a fix that reached for the CMake ``cname``
would render ``commz.dsp_filters`` and pass a flat-module-only test.

The value a synthesized example *predicts* is a separate question, deliberately
not asserted here -- see gh-1212.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit._init import run as init_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._stubs import _title  # noqa: E402

PKG = "commz"

# `>>> from <path> import <Name>` wherever it is embedded -- a `.pyi` carries
# it as source, a `_ext.c` as a C string literal, so the pattern anchors on the
# doctest prompt rather than on line structure.
_IMPORT_RE = re.compile(r">>> from ([\w.]+) import (\w+)")


def _scaffold(root: Path) -> Path:
    """A project carrying all three placements, built by running the tool."""
    proj = root / PKG
    new_run(
        PKG,
        proj,
        object_names=[],
        state_vars=[],
        arg_type="uint64_t",
        return_type="uint64_t",
        pytest_=False,
        pytest_benchmark_=False,
    )
    # standalone: the bare form is CORRECT here and must stay
    init_run(
        proj,
        "solo",
        state_vars=[("gain", "uint64_t", "1")],
        arg_type="uint64_t",
        return_type="uint64_t",
    )
    for module_id in ("dsp", "sub.nested"):
        module_run(proj, module_id)
        obj = "counter" if module_id == "dsp" else "ticker"
        object_run(
            proj,
            obj,
            module=module_id,
            state_vars=[("gain", "uint64_t", "1")],
            arg_type="uint64_t",
            return_type="uint64_t",
        )
        # a named method reaches a different synthesizer than step()/steps()
        method_run(
            proj,
            obj,
            "tally",
            module=module_id,
            arg_type="uint64_t",
            return_type="uint64_t",
            variable_output=False,
            multi_output=[],
        )
    return proj


def _expected_path(proj: Path, obj: str, cfg: dict) -> str:
    """Where *obj*'s class is importable from, per the tree jm actually wrote.

    The stub a component's class is declared in sits in the package directory
    that class imports from, so the answer is that directory relative to
    ``src/``, dotted. Independent of every expression under test.
    """
    module_id = C.module_of(cfg, obj)
    leaf = C.module_paths(module_id).leaf if module_id else obj
    stub = next(p for p in (proj / "src").rglob(f"{leaf}.pyi") if p.is_file())
    return ".".join(stub.parent.relative_to(proj / "src").parts)


def _faces(proj: Path, obj: str, cfg: dict) -> list[Path]:
    """Both generated faces that can carry a doctest for *obj*."""
    module_id = C.module_of(cfg, obj)
    if module_id:
        cname = C.module_paths(module_id).cname
        leaf = C.module_paths(module_id).leaf
        native = proj / "native" / "src" / cname
        return [
            *(p for p in native.glob(f"{cname}_ext*{obj}*.c")),
            *(p for p in (proj / "src").rglob(f"{leaf}.pyi")),
        ]
    return [
        proj / "native" / "src" / obj / f"{obj}_ext.c",
        proj / "src" / PKG / f"{obj}.pyi",
    ]


def test_every_synthesized_doctest_imports_from_where_the_class_lands(
    tmp_path,
):
    proj = _scaffold(tmp_path)
    cfg = C.load(proj)

    objects = sorted(
        {o for m in C.modules(cfg) for o in C.module_objects(cfg, m)}
        | set(C.components(cfg))
    )
    assert len(objects) >= 3, f"fixture lost a placement: {objects}"

    checked = 0
    wrong: list[str] = []
    for obj in objects:
        want = _expected_path(proj, obj, cfg)
        # same fallback the stub writer uses: an explicit class name is an
        # override, and `class_name` is None without one.
        Component = C.class_name(cfg, obj) or _title(obj)
        for face in _faces(proj, obj, cfg):
            if not face.exists():
                continue
            for got, name in _IMPORT_RE.findall(
                face.read_text(encoding="utf-8")
            ):
                if name != Component:
                    continue
                checked += 1
                if got != want:
                    wrong.append(
                        f"{face.relative_to(proj)}: "
                        f"`from {got} import {name}` but {name} lands in "
                        f"{want!r}"
                    )

    # An empty set reads as green, and this whole bug hid behind examples that
    # nothing executed -- so require that the sweep actually saw some.
    assert checked >= 6, f"only {checked} import line(s) examined"
    assert not wrong, "synthesized doctest imports from the wrong path:\n" + (
        "\n".join(wrong)
    )


def test_a_standalone_object_keeps_the_bare_import():
    """The bare form is not the bug; not asking the question is.

    Pinned separately because a fix that unconditionally inserted a segment
    would satisfy the sweep above on module objects and silently break every
    standalone one.
    """
    from just_makeit._docstring import class_import_line, class_import_path

    assert class_import_path("commz") == "commz"
    assert class_import_path("commz", "dsp") == "commz.dsp"
    assert class_import_path("commz", "sub.nested") == "commz.sub.nested"
    assert class_import_line("commz", "Solo") == "from commz import Solo"
    assert (
        class_import_line("commz", "Counter", "dsp")
        == "from commz.dsp import Counter"
    )

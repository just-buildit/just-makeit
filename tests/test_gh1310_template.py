"""A C type template: one manifest declaration, N components (gh-1310, PR 1).

doppler's `cvt` spells twelve near-identical converters as twelve manifests.
`[template.X]` declares the family once:

- ``params`` is a CLOSED list, and an instance row may set exactly those keys
  plus ``id``. That single rule is what makes a diverging sibling
  unrepresentable rather than detectable -- an instance has no syntax in which
  to disagree about ``mutable`` or gain a property its siblings lack.
- ``{param}`` interpolates into VALUES only, never keys or table names, so the
  manifest parses and reads without expansion.
- Expansion happens in ``load``; ``save`` folds it back. Nothing is written
  per instance, so a round-trip can never recreate the twelve tables.
- A verb run on an instance is refused -- by the member-declaring verbs before
  they write any C, and by ``save`` for everything else -- because folding it
  back would otherwise drop the edit silently.
- ``jm script`` names the template instead of replaying N ``jm object`` lines.

This PR covers the manifest half only. The C half -- one family header so the
twelve cores cannot drift either -- is PR 2, and until it lands the docs claim
only that the MANIFESTS cannot diverge.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _status  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

from _jmrun import JmRun, run_cli

_HAVE_TOOLCHAIN = bool(shutil.which("cmake")) and any(
    shutil.which(c) for c in ("cc", "gcc", "clang")
)

TEMPLATE = """
[template.f32_to_int]
params = ["elem", "Elem", "scale"]
module = "cvt"
arg_type = "float"
return_type = "{elem}"
class_name = "F32To{Elem}"

[[template.f32_to_int.init_params]]
name = "scale"
type = "float"
default = "{scale}"

[[template.f32_to_int.instances]]
id = "f32_to_i16"
elem = "int16_t"
Elem = "I16"
scale = "32768.0f"

[[template.f32_to_int.instances]]
id = "f32_to_i8"
elem = "int8_t"
Elem = "I8"
scale = "128.0f"
"""


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(io.StringIO()):
            return fn(*a, **kw)


def _project(tmp_path: Path, template: str = TEMPLATE) -> Path:
    root = tmp_path / "p"
    _quiet(new_run, "p", root)
    _quiet(module_run, root, "cvt")
    _quiet(object_run, root, "other", None, state_vars=[("n", "int", "0")])
    toml = root / C.FILENAME
    toml.write_text(toml.read_text(encoding="utf-8") + template, "utf-8")
    _quiet(apply_run, root)
    return root


class TestExpansion:
    def test_every_instance_is_an_ordinary_component(self, tmp_path):
        cfg = C.load(_project(tmp_path))
        for iid, elem in (("f32_to_i16", "int16_t"), ("f32_to_i8", "int8_t")):
            assert cfg[iid]["return_type"] == elem
            assert iid in C.module_objects(cfg, "cvt")
        assert cfg["f32_to_i16"]["class_name"] == "F32ToI16"
        assert cfg["f32_to_i8"]["init_params"][0]["default"] == "128.0f"

    def test_the_artefacts_are_generated_and_status_is_clean(self, tmp_path):
        root = _project(tmp_path)
        pyi = (root / "src" / "p" / "cvt" / "cvt.pyi").read_text()
        assert "class F32ToI16:" in pyi and "class F32ToI8:" in pyi
        assert _quiet(_status.run, root, check=True) == 0


class TestNothingIsWrittenPerInstance:
    def _instance_tables(self, root: Path) -> "list[str]":
        texts = [(root / C.FILENAME).read_text()] + [
            p.read_text() for p in root.glob("objects/*.toml")
        ]
        return [t for t in texts if "[f32_to_i16]" in t or "[f32_to_i8]" in t]

    def test_apply_leaves_only_the_template(self, tmp_path):
        root = _project(tmp_path)
        assert self._instance_tables(root) == []
        assert "[template.f32_to_int]" in (root / C.FILENAME).read_text()
        assert "f32_to_i16" not in str(C.load_manifest(root).get("module"))

    def test_a_verb_on_another_component_keeps_it_folded(self, tmp_path):
        root = _project(tmp_path)
        _quiet(
            method_run, root, "other", "ping", None, "void", "void", False, []
        )
        assert self._instance_tables(root) == []
        assert "ping" in str(C.load(root)["other"])


class TestTheClosedParamsList:
    @pytest.mark.parametrize(
        "row, says",
        [
            (
                'id = "x"\nelem = "int8_t"\nElem = "X"\nscale = "1.0f"\n'
                'mutable = "true"\n',
                "instances may set only",
            ),
            (
                'id = "x"\nelem = "int8_t"\nElem = "X"\n',
                "does not set `scale`",
            ),
            ('elem = "int8_t"\nElem = "X"\nscale = "1.0f"\n', "has no `id`"),
        ],
        ids=["extra-key", "missing-param", "no-id"],
    )
    def test_a_bad_instance_row_is_refused(self, tmp_path, row, says):
        bad = TEMPLATE + "\n[[template.f32_to_int.instances]]\n" + row
        root = tmp_path / "p"
        _quiet(new_run, "p", root)
        _quiet(module_run, root, "cvt")
        (root / C.FILENAME).write_text(
            (root / C.FILENAME).read_text() + bad, "utf-8"
        )
        err = io.StringIO()
        with contextlib.redirect_stderr(err), pytest.raises(SystemExit):
            C.load(root)
        assert says in err.getvalue()

    def test_a_slot_naming_no_param_is_refused(self, tmp_path):
        bad = TEMPLATE.replace('"F32To{Elem}"', '"F32To{Elme}"')
        root = tmp_path / "p"
        _quiet(new_run, "p", root)
        _quiet(module_run, root, "cvt")
        (root / C.FILENAME).write_text(
            (root / C.FILENAME).read_text() + bad, "utf-8"
        )
        err = io.StringIO()
        with contextlib.redirect_stderr(err), pytest.raises(SystemExit):
            C.load(root)
        assert "`{Elme}` is not one of its params" in err.getvalue()


class TestAnInstanceCannotBeEditedDirectly:
    def test_a_member_verb_refuses_before_writing_c(self, tmp_path):
        root = _project(tmp_path)
        core = root / "native" / "src" / "f32_to_i16" / "f32_to_i16_core.c"
        before = core.read_text()
        with pytest.raises(SystemExit):
            _quiet(
                method_run,
                root,
                "f32_to_i16",
                "tweak",
                "cvt",
                "void",
                "void",
                False,
                [],
            )
        assert core.read_text() == before

    def test_save_refuses_any_other_route(self, tmp_path):
        root = _project(tmp_path)
        cfg = C.load(root)
        cfg["f32_to_i16"]["mutable"] = "true"
        with pytest.raises(SystemExit):
            _quiet(C.save, root, cfg)
        assert "mutable" not in str(C.load(root)["f32_to_i16"])


def test_script_names_the_template_instead_of_replaying_instances(tmp_path):
    from just_makeit._script import run as script_run

    root = _project(tmp_path)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        script_run(root)
    text = out.getvalue()
    assert "[template.f32_to_int]" in text
    assert "just-makeit object f32_to_i16" not in text


def _cli(*args, cwd) -> JmRun:
    # gh-1374: in THIS process -- the child bought isolation only.
    return run_cli(*args, cwd=cwd)


@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="needs cmake and a C compiler")
def test_the_instances_build_and_pass(tmp_path):
    r = _cli("test", cwd=_project(tmp_path))
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]

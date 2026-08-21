"""gh-1064: a record declaration jm accepts must be one it can generate.

`result_fields` names the columns of a record, and jm produces three different
results from it -- ONE record (`single`), an ARRAY of records
(`record_dtype`), or a `list[tuple]` (neither). Nothing checked that the
declaration described any of them. The binding is built from the shape and the
prototype from the return type; the two were never compared, so a declaration
outside the three shapes was accepted, exited 0, and emitted C that does not
compile.

The rules are derived from measuring every combination rather than from
reading the generator, and the table in `_records.validate_record_shape`
records what each one did. Two of them are checked here; the point of the
matrix below is that the VALID shapes still build, because a validator is only
as good as the things it declines to reject.

Both faces are covered. `jm method` and `jm function` each accept
`result_fields` and each emitted the identical broken binding from the
identical bad declaration -- gh-1060 is what fixing one of a pair looks like a
release later.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._function import run as function_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._record import validate_record_shape  # noqa: E402

_NO_TOOLCHAIN = shutil.which("cmake") is None or (
    shutil.which("cc") is None and shutil.which("gcc") is None
)

_FIELDS = [
    {"name": "i", "type": "size_t"},
    {"name": "v", "type": "double"},
]

_ROW_STRUCT = "typedef struct { size_t i; double v; } row_t;\n\n"


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _project(tmp_path: Path) -> Path:
    proj = tmp_path / "demo"
    _quiet(new_run, "demo", proj)
    _quiet(
        object_run,
        proj,
        "col",
        None,
        state_vars=[("n", "uint64_t", "0")],
        arg_type="double",
        return_type="void",
    )
    header = proj / "native" / "inc" / "col" / "col_core.h"
    text = header.read_text(encoding="utf-8")
    anchor = text.index("typedef struct {")
    header.write_text(
        text[:anchor] + _ROW_STRUCT + text[anchor:], encoding="utf-8"
    )
    return proj


class TestTheRulesThemselves:
    """The predicate, over the whole measured truth table."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"return_type": "double"},
            {"return_type": "void"},
            {"return_type": "uint8_t"},
            {"return_type": "double", "single": True},
            {"return_type": "row_t", "variable_output": True},
            {"return_type": "double", "variable_output": True},
            {"return_type": "void", "variable_output": True},
        ],
        ids=[
            "scalar",
            "void",
            "other-scalar",
            "single-scalar",
            "struct+varout",
            "scalar+varout",
            "void+varout",
        ],
    )
    def test_a_shape_jm_cannot_generate_is_refused(self, kwargs):
        assert validate_record_shape(
            "method",
            "m",
            result_fields=_FIELDS,
            **{"return_type": kwargs.pop("return_type"), **kwargs},
        )

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"return_type": "row_t"},
            {"return_type": "row_t", "single": True},
            {"return_type": "double", "record_dtype": "row_t"},
            {"return_type": "row_t", "record_dtype": "row_t"},
            {"return_type": "void", "record_dtype": "row_t"},
            {
                "return_type": "double",
                "record_dtype": "row_t",
                "variable_output": True,
            },
        ],
        ids=[
            "row-struct",
            "single-struct",
            "dtype-scalar",
            "dtype-struct",
            "dtype-void",
            "dtype+varout",
        ],
    )
    def test_a_shape_jm_can_generate_is_allowed(self, kwargs):
        """The half that matters most.

        With `record_dtype` the return type is deliberately unconstrained --
        a scalar, a struct and `void` were each measured to build, because the
        out-parameter carries the shape and the return value is the count. A
        rule that also rejected those would be indistinguishable from this one
        on the failing cases and would break three working shapes.
        """
        assert not validate_record_shape(
            "method",
            "m",
            result_fields=_FIELDS,
            **{"return_type": kwargs.pop("return_type"), **kwargs},
        )

    def test_a_method_with_no_result_fields_is_never_touched(self):
        assert not validate_record_shape("method", "m", "double", [])
        assert not validate_record_shape("method", "m", "void", None)


class TestTheCommandsRefuse:
    """Reached through the real entry points, which `jm apply` replays."""

    def test_method_exits_nonzero(self, tmp_path):
        proj = _project(tmp_path)
        with pytest.raises(SystemExit) as e:
            _quiet(
                method_run,
                proj,
                "col",
                "peaks",
                None,
                "void",
                "double",
                False,
                [],
                result_fields=_FIELDS,
            )
        assert e.value.code == 1

    def test_function_exits_nonzero(self, tmp_path):
        proj = tmp_path / "demo"
        _quiet(new_run, "demo", proj)
        _quiet(module_run, proj, "dsp")
        with pytest.raises(SystemExit) as e:
            _quiet(
                function_run,
                proj,
                "peaks",
                "dsp",
                params=[("n", "size_t")],
                return_type="double",
                result_fields=_FIELDS,
            )
        assert e.value.code == 1

    def test_nothing_was_written_before_the_refusal(self, tmp_path):
        """It refuses BEFORE generating, not after.

        A check that fired late would leave a half-written component behind
        and a manifest entry for a method that does not exist.
        """
        proj = _project(tmp_path)
        before = sorted(
            p.name for p in (proj / "native" / "src" / "col").iterdir()
        )
        manifest_before = (proj / "just-makeit.toml").read_text(
            encoding="utf-8"
        )
        with contextlib.suppress(SystemExit):
            _quiet(
                method_run,
                proj,
                "col",
                "peaks",
                None,
                "void",
                "double",
                False,
                [],
                result_fields=_FIELDS,
            )
        after = sorted(
            p.name for p in (proj / "native" / "src" / "col").iterdir()
        )
        assert before == after
        assert (proj / "just-makeit.toml").read_text(
            encoding="utf-8"
        ) == manifest_before


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
class TestTheAllowedShapesStillBuild:
    """The guard against over-fixing: rejecting everything also passes above."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"return_type": "row_t"},
            {
                "return_type": "double",
                "record_dtype": "row_t",
                "variable_output": True,
            },
        ],
        ids=["row-struct", "record-dtype"],
    )
    def test_it_compiles(self, tmp_path, kwargs):
        proj = _project(tmp_path)
        _quiet(
            method_run,
            proj,
            "col",
            "peaks",
            None,
            "void",
            kwargs.pop("return_type"),
            kwargs.pop("variable_output", False),
            [],
            result_fields=_FIELDS,
            **kwargs,
        )
        cfg = subprocess.run(
            [
                "cmake",
                "-S",
                str(proj),
                "-B",
                str(proj / "build"),
                f"-DPython3_EXECUTABLE={sys.executable}",
            ],
            capture_output=True,
            text=True,
        )
        assert cfg.returncode == 0, cfg.stdout + cfg.stderr
        built = subprocess.run(
            ["cmake", "--build", str(proj / "build"), "--parallel", "4"],
            capture_output=True,
            text=True,
        )
        assert built.returncode == 0, built.stdout + built.stderr

"""
gh-610 — the benchmark scaffold (and the .pyi construction doctest it shares
a rendering path with) constructed the object **positionally**, and a
``bool`` init-param default rendered as the C/TOML literal ``true`` instead
of Python's ``True``.

Two consequences, one cosmetic and one severe:

1. A positional call rots silently on any kwlist reorder — jm itself moved
   ``string_enum`` params out of a fixed hoisted position (gh-422), and any
   scaffold generated under the old order kept compiling while starting to
   mean something different (wrong values landing on the wrong params, no
   error).
2. The ``true`` literal is not just a bench-fixture wart: it landed directly
   in the generated ``.pyi``'s constructor **signature**
   (``def __init__(self, flag: bool = true) -> None: ...``), which is
   invalid Python — the whole stub fails to import/parse, not just the
   construction example.

Fix: construction examples (bench fixture, docstring doctest) are emitted as
keyword arguments — immune to reorder by construction, and self-documenting.
``_py_default``/``_py_default_stub`` translate a bool default through
Python's ``True``/``False`` rather than passing the C spelling through
verbatim. (An empty float default's existing ``.0`` literal is deliberately
left alone — it is valid Python and a real, working example; that is a
separate, intentional design choice, not this bug.)
"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._context._types import _py_default
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._stubs import _py_default_stub


class TestPyDefaultBoolAndFloat:
    """Unit tests on the literal-rendering helpers directly."""

    def test_bool_true_renders_python_true(self):
        assert _py_default("bool", "true") == "True"
        assert _py_default_stub("bool", "true") == "True"

    def test_bool_false_renders_python_false(self):
        assert _py_default("bool", "false") == "False"
        assert _py_default_stub("bool", "false") == "False"

    def test_bool_case_insensitive(self):
        assert _py_default("bool", "TRUE") == "True"
        assert _py_default_stub("bool", "False") == "False"

    def test_bool_empty_default_is_sentinel(self):
        assert _py_default("bool", "") == "..."
        assert _py_default_stub("bool", "") == "..."

    def test_float_empty_default_still_synthesises_dot_zero(self):
        # Deliberately unchanged: an absent float default already produces
        # the valid (if unusual-looking) literal `.0` — a real, working
        # example. Converting it to the `...` sentinel would suppress a
        # working construction example for no reason; not this issue's bug.
        assert _py_default("float", "") == ".0"

    def test_float_non_empty_default_unaffected(self):
        assert _py_default("float", "0.0") == "0.0"
        assert _py_default("float", "1.5f") == "1.5"
        assert _py_default("float", "2") == "2.0"


_PARAMS = [
    ("sample_rate_hz", "float", "0.0"),
    ("symbol_rate_hz", "float", "0.0"),
    ("resolution_hz", "float", "0.0"),
    ("window", "string_enum:hann,kaiser", "hann"),
    ("sequential", "bool", "true"),
    ("max_n_blocks", "int", "100000"),
]


def _scaffold(tmp_path, pytest_benchmark=True):
    root = tmp_path / "dsp"
    new_run("dsp", root, pytest_benchmark_=pytest_benchmark)
    object_run(root, "carrier_acq", None, no_state=True, init_params=_PARAMS)
    return root


def _bench_py(root):
    return root / "src" / "dsp" / "benchmarks" / "bench_carrier_acq.py"


def _pyi(root):
    return (root / "src" / "dsp" / "carrier_acq.pyi").read_text(
        encoding="utf-8"
    )


class TestBenchFixtureIsKeywordAndValid:
    def test_bench_fixture_uses_keywords(self, tmp_path):
        root = _scaffold(tmp_path)
        src = _bench_py(root).read_text(encoding="utf-8")
        assert "CarrierAcq(" in src
        call_line = next(ln for ln in src.splitlines() if "CarrierAcq(" in ln)
        assert "sample_rate_hz=" in call_line
        assert "sequential=True" in call_line
        assert "true" not in call_line
        # The bench file itself must be valid, importable Python.
        ast.parse(src)

    def test_bench_fixture_order_independent_of_reorder(self, tmp_path):
        # Reordering init_params must not change which value lands where —
        # keyword construction means the call text always names its target.
        reordered = list(reversed(_PARAMS))
        root = tmp_path / "dsp"
        new_run("dsp", root, pytest_benchmark_=True)
        object_run(
            root, "carrier_acq", None, no_state=True, init_params=reordered
        )
        src = _bench_py(root).read_text(encoding="utf-8")
        call_line = next(ln for ln in src.splitlines() if "CarrierAcq(" in ln)
        assert "sequential=True" in call_line
        assert "max_n_blocks=100000" in call_line


class TestPyiConstructorSignatureIsValid:
    def test_bool_default_in_signature_is_valid_python(self, tmp_path):
        root = _scaffold(tmp_path)
        pyi = _pyi(root)
        assert "sequential: bool = True" in pyi
        assert "= true" not in pyi
        ast.parse(pyi)  # the whole stub must parse

    def test_doctest_example_is_keyword_and_matches_values(self, tmp_path):
        root = _scaffold(tmp_path)
        pyi = _pyi(root)
        example = next(
            ln for ln in pyi.splitlines() if ">>> obj = CarrierAcq(" in ln
        )
        assert "sequential=True" in example
        assert "window=" in example


class TestModuleAggregatedPyiUnaffectedRegression:
    """The module-aggregated .pyi builder already emitted keyword args for
    init_params before gh-610 (a pre-existing, correct implementation) —
    this locks that in as a regression guard, and confirms its bool
    rendering is fixed too."""

    def test_module_pyi_keeps_keyword_args_and_true_bool(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root)
        module_run(root, "mod")
        object_run(
            root,
            "carrier_acq",
            module="mod",
            no_state=True,
            init_params=_PARAMS,
        )
        pyi = (root / "src" / "dsp" / "mod" / "mod.pyi").read_text(
            encoding="utf-8"
        )
        example = next(
            ln for ln in pyi.splitlines() if ">>> obj = CarrierAcq(" in ln
        )
        assert "sequential=True" in example
        assert "true" not in example
        ast.parse(pyi)


class TestStateVarsOnlyShapeAlsoKeyword:
    """gh-610: the [[state]]-only constructor shape (no init_params) gets
    the same keyword treatment as the init_params shape."""

    def test_state_vars_bench_fixture_uses_keywords(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, pytest_benchmark_=True)
        object_run(
            root,
            "agc",
            None,
            state_vars=[
                ("gain", "float", "1.0f"),
                ("enabled", "bool", "true"),
            ],
            arg_type="float _Complex",
            return_type="float _Complex",
        )
        src = (root / "src" / "dsp" / "benchmarks" / "bench_agc.py").read_text(
            encoding="utf-8"
        )
        call_line = next(ln for ln in src.splitlines() if "Agc(" in ln)
        assert "gain=" in call_line
        assert "enabled=True" in call_line
        ast.parse(src)

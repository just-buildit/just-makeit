"""A ``batch`` method row carries only the keys its binding reads (gh-2261).

A batch method is a 1:1-rate block transform, and ``make_methods_ctx``
renders it ahead of every other result shape. So any other shape key on its
row was stored in the manifest and read by nobody: ``pass_capacity``,
``exact_max_out``, ``max_out``, ``out_divisor``, ``status_return`` and
``error_negative`` (this issue), a param (gh-2021), ``out_type`` (gh-1991),
``variable_output`` (gh-2200, which did not even compile) and
``none_on_empty`` (gh-2202). A record return crashed with a bare
``KeyError`` from ``_CTYPE_META``.

The answer is one ALLOW-list, `_keys.BATCH_METHOD_KEYS`, asked by
`_method.batch_why_not` from `_method.run` -- which `jm method` and
``apply``'s replay both call. What this file holds:

1. **Registration-free.** Every key of `_keys.METHOD_KEYS` outside the
   allow-list, put on a batch row, is refused by ``apply`` with one
   ``error:`` line naming it, and nothing is written. The keys come from the
   registry, so a method key added later is refused on a batch row (and this
   test needs a sample value for it) until someone adds it to the allow-list.
2. **The CLI.** The issue's trigger and its four siblings, refused before
   anything is written.
3. **What a batch row may carry still works**, through both doors.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from _jmrun import run_cli  # noqa: E402

from just_makeit import _keys  # noqa: E402
from just_makeit._method import _SIGNATURE_COERCIONS  # noqa: E402

#: The keys a batch row may NOT carry, read off the registry.
REFUSED_KEYS = sorted(_keys.METHOD_KEYS - _keys.BATCH_METHOD_KEYS)

#: A TOML value for a key whose sample cannot be derived from its type --
#: lists of tables, and strings the manifest loader parses. Every other key
#: gets one from its `_SIGNATURE_COERCIONS` default (`_sample`).
_SAMPLES = {
    "params": '[{name = "k", type = "int"}]',
    "extra_args": '[{name = "k", type = "int"}]',
    "multi_output": '["int"]',
    "result_fields": '[{name = "a", type = "double"}]',
    "status_errors": '[{value = 1, error = "ValueError"}]',
    "releases": '["peek"]',
    "count_type": '"int64_t"',
    "error_sentinel": '"SIZE_MAX"',
    "out_type": '"float"',
    "out_cols": '"2"',
    "error": '"OSError"',
    "py_return_type": '"float"',
    "record_doc": '"A record."',
}


#: Keys whose own load-time rule (`_config.manifest_type_errors`) refuses
#: them off a variable_output / list-of-records method, which a batch method
#: is not -- so `apply` refuses them before its replay reaches the batch
#: table. `jm method` asks the table first.
_LOAD_RULES = {"consumed_fn", "no_count", "out_cols", "short_out"}


def _sample(key: str) -> str:
    """A TOML value for *key* that differs from its default."""
    if key in _SAMPLES:
        return _SAMPLES[key]
    coerce, default = _SIGNATURE_COERCIONS[key]
    if coerce is bool:
        return "true"
    if coerce is int:
        return str(default + 2)
    return '"x_fn"'


def _snapshot(root: Path) -> "dict[str, bytes]":
    """Every file under *root*."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


@pytest.fixture(scope="module")
def blank(tmp_path_factory) -> Path:
    """A project with one object, built once and copied per test."""
    base = tmp_path_factory.mktemp("gh2261")
    assert run_cli("new", "p", "--no-c-prefix", cwd=base).returncode == 0
    r = run_cli("object", "o", cwd=base / "p")
    assert r.returncode == 0, r.stdout + r.stderr
    return base / "p"


@pytest.fixture
def project(blank, tmp_path) -> Path:
    shutil.copytree(blank, tmp_path / "p")
    return tmp_path / "p"


def _error_lines(stderr: str) -> "list[str]":
    return [ln for ln in stderr.splitlines() if ln.startswith("error:")]


# -- 1. every key outside the allow-list, through `apply` ---------------------


def test_the_allow_list_is_method_keys():
    """Armed: the table names real keys, and leaves most of them out."""
    assert _keys.BATCH_METHOD_KEYS <= _keys.METHOD_KEYS
    assert "batch" in _keys.BATCH_METHOD_KEYS
    assert len(REFUSED_KEYS) >= 30, REFUSED_KEYS


@pytest.mark.parametrize("key", REFUSED_KEYS)
def test_apply_refuses_every_key_a_batch_row_does_not_read(project, key):
    frag = project / "objects" / "o.toml"
    frag.write_text(
        frag.read_text("utf-8")
        + '\n[[o.methods]]\nname = "blk"\narg_type = "float"\n'
        f'return_type = "float"\nbatch = true\n{key} = {_sample(key)}\n',
        encoding="utf-8",
    )
    before = _snapshot(project)
    r = run_cli("apply", cwd=project)
    assert r.returncode == 1, f"{key}: accepted\n{r.stdout}{r.stderr}"
    assert "Traceback" not in r.stderr, r.stderr
    assert len(_error_lines(r.stderr)) == 1, r.stderr
    # `apply` funnels `extra_args` into `params`, so that is what it names.
    named = {"extra_args": "params"}.get(key, key)
    if "no effect on a batch method" in r.stderr:
        assert f"{named} has no effect" in r.stderr, r.stderr
    else:
        # Refused at load by the key's own rule, ahead of the replay: the
        # message names the key, and the batch table never had to answer.
        assert key in _LOAD_RULES, f"{key}: refused for another reason"
        assert f"{named} " in r.stderr, r.stderr
    assert _snapshot(project) == before, f"{key}: apply wrote before refusing"


# -- 2. the CLI ----------------------------------------------------------------

_B = "--arg-type float --return-type float --batch"

#: (id, flags, the key the refusal names): the issue's trigger, then the
#: four sibling issues' batch halves.
CLI = [
    ("pass-capacity", f"{_B} --pass-capacity", "pass_capacity"),
    ("exact-max-out", f"{_B} --exact-max-out", "exact_max_out"),
    ("max-out", f"{_B} --max-out 8", "max_out"),
    ("out-divisor", f"{_B} --out-divisor 2", "out_divisor"),
    (
        "status-return",
        "--arg-type float --return-type int --batch --status-return",
        "status_return",
    ),
    (
        "error-negative",
        "--arg-type float --return-type int --batch --error-negative",
        "error_negative",
    ),
    (
        "result-field-keyerror",
        "--arg-type float --return-type rec_t --batch --result-field a:double",
        "result_fields",
    ),
    ("gh-2200", f"{_B} --variable-output", "variable_output"),
    ("gh-2021", f"{_B} --param k:int", "params"),
    ("gh-1991", f"{_B} --out-type uint8_t", "out_type"),
    ("gh-2202", f"{_B} --none-on-empty", "none_on_empty"),
    ("gh-1540", f"{_B} --multi-output int", "multi_output"),
]


@pytest.mark.parametrize(
    "flags, key", [c[1:] for c in CLI], ids=[c[0] for c in CLI]
)
def test_jm_method_refuses_before_anything_is_written(project, flags, key):
    before = _snapshot(project)
    r = run_cli("method", "o", "m", *flags.split(), cwd=project)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    errors = _error_lines(r.stderr)
    assert len(errors) == 1, r.stderr
    assert f"method 'o.m': {key} has no effect on a batch" in errors[0]
    # It names what a batch method does read.
    assert "reads only arg_type" in errors[0], errors[0]
    assert _snapshot(project) == before, "a refused `jm method` wrote files"


# -- 3. what a batch row may carry --------------------------------------------


def test_the_cli_keeps_every_key_a_batch_method_reads(project):
    r = run_cli(
        "method", "o", "blk", *_B.split(), "--nogil", "--strict",
        "--doc", "A block.", cwd=project,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli(
        "method", "o", "gen", "--arg-type", "void", "--return-type",
        "float", "--batch", "--count-default", "8", "--count-name", "n",
        cwd=project,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert run_cli("apply", cwd=project).returncode == 0


# -- 4. the element: a scalar, resolved ---------------------------------------

#: A record struct, and a declared SCALAR element (gh-1404) -- the name a
#: batch return may spell, because it resolves to its C type.
_RECORDS = (
    '\n[[o.records]]\nname = "rec_t"\n[[o.records.fields]]\n'
    'name = "a"\ntype = "double"\n'
    '\n[[o.records]]\nname = "sample"\ntype = "float"\n'
)


def _batch_row(project: Path, return_type: str) -> None:
    frag = project / "objects" / "o.toml"
    frag.write_text(
        frag.read_text("utf-8")
        + '\n[[o.methods]]\nname = "blk"\narg_type = "float"\n'
        f'return_type = "{return_type}"\nbatch = true\n' + _RECORDS,
        encoding="utf-8",
    )


def test_a_record_return_is_refused_not_a_key_error(project):
    """The issue's `KeyError`: the element of a batch result is a scalar."""
    _batch_row(project, "rec_t")
    before = _snapshot(project)
    r = run_cli("apply", cwd=project)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    errors = _error_lines(r.stderr)
    assert len(errors) == 1, r.stderr
    assert (
        "is batch, but its output element 'rec_t' is not a scalar"
        in (errors[0])
    ), r.stderr
    assert _snapshot(project) == before, "apply wrote before refusing"


def test_a_declared_scalar_element_is_its_c_type(project):
    _batch_row(project, "sample")
    r = run_cli("apply", cwd=project)
    assert r.returncode == 0, r.stdout + r.stderr

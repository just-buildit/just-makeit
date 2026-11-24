"""A record name is checked, and stubbed, the way its ``.so`` holds it.

gh-2755 moved gh-1268's "one public name, one shape" check to the manifest
load, which made its scope matter: a refusal there blocks every command.

* The scope is the extension module the C is assembled into. A view placed
  in ANOTHER module (gh-2516) shares nothing with its parent's module, and a
  view another module places HERE does, inherited methods included. The
  check listed every view of the module's objects wherever it lived, and
  none placed in from outside.
* The first claim on a name wins, and the stub has to agree with the
  runtime on which one that is. The aggregator registers every object, then
  every view, then the functions; the stub walked each object's methods and
  then that object's views, so a view and a LATER object sharing a record
  with different ``record_doc`` documented the view's in the stub and the
  object's in ``help()``.
"""

from __future__ import annotations

import copy

from _jmrun import run_cli

from just_makeit import _config as C


def _rec(name: str, field: str, ctype: str, **extra) -> dict:
    return {
        "name": name,
        "single": True,
        "record_name": "R",
        "return_type": "r_t",
        "result_fields": [{"name": field, "type": ctype}],
        **extra,
    }


#: `a` (module x) publishes R(a: double); its view lives in module y,
#: leaves `r` out and declares its own R(z: int). The two never share a .so.
APART = {
    "project": {"name": "p"},
    "module": {"x": {"objects": ["a"]}, "y": {"objects": ["b"]}},
    "a": {
        "methods": [_rec("r", "a", "double")],
        "views": [
            {
                "class_name": "AV",
                "create_fn": "av_create",
                "module": "y",
                "exclude_methods": ["r"],
                "methods": [_rec("r2", "z", "int")],
            }
        ],
    },
    "b": {},
}


def test_a_view_in_another_module_is_not_a_clash():
    assert C.function_record_errors(APART) == []


def test_the_same_view_at_home_is_a_clash():
    cfg = copy.deepcopy(APART)
    del cfg["a"]["views"][0]["module"]
    (err,) = C.function_record_errors(cfg)
    assert err.startswith("[module.x] two records share the public name 'R'")


def test_a_view_placed_here_clashes_with_this_module():
    cfg = copy.deepcopy(APART)
    cfg["b"]["methods"] = [_rec("q", "n", "uint8_t")]
    (err,) = C.function_record_errors(cfg)
    assert err.startswith("[module.y] two records share the public name 'R'")


def test_a_view_placed_here_brings_its_inherited_records():
    # Without the exclusion the view inherits `r`, R(a: double), into y
    # beside its own R(z: int): one .so, two shapes.
    cfg = copy.deepcopy(APART)
    del cfg["a"]["views"][0]["exclude_methods"]
    (err,) = C.function_record_errors(cfg)
    assert err.startswith("[module.y] two records share the public name 'R'")


# -- the first claim, on both faces ---------------------------------------

ORDER = """\
[module.m]
objects = ["a", "b"]

[[a.views]]
class_name = "AV"
create_fn = "av_create"
methods = [{ name = "rv", single = true, record_name = "Rs", \
record_doc = "V", return_type = "rs_t", \
result_fields = [{ name = "x", type = "double" }] }]

[[b.methods]]
name = "rb"
single = true
record_name = "Rs"
record_doc = "B"
return_type = "rs_t"
result_fields = [{ name = "x", type = "double" }]
"""


def test_the_stub_documents_the_record_the_runtime_registers(tmp_path):
    assert run_cli("new", "p", "--no-c-prefix", cwd=tmp_path).returncode == 0
    dest = tmp_path / "p"
    frag = tmp_path / "frag.toml"
    frag.write_text(ORDER, encoding="utf-8")
    r = run_cli("apply", str(frag), cwd=dest)
    assert r.returncode == 0, r.stdout + r.stderr
    ext = (dest / "native/src/m/m_ext.c").read_text("utf-8")
    # The runtime publishes b's type: every object registers before a view.
    assert 'PyModule_AddObject(m, "Rs", (PyObject *)B_rb_type)' in ext, ext
    pyi = (dest / "src/p/m/m.pyi").read_text("utf-8")
    cls = pyi[pyi.index("class Rs(") :]
    cls = cls[: cls.index("\n\n")]
    assert '"""B"""' in cls, cls

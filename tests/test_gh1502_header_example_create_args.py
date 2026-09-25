"""gh-1502: the ``_core.h`` example's create() call cannot go stale silently.

GATE: the header's @code example names create()'s parameters, never values.
GATE: `jm status` reports (advisory, exit 0) an example whose create() arg count differs from the prototype.

The file comment at the top of every object's sacred ``_core.h`` carries an
``@code`` example. It used to freeze the scaffold-time constructor values
into the call::

    jm object o --no-step --state threshold:int:20
    # add [[o.init_params]] level, then `jm apply`
    * o_state_t *obj = o_create(20);     <- the example, never rewritten
    o_state_t *o_create(int level);      <- the prototype, which apply moved

Two halves, one per decision on the issue:

* **Render.** The example declares one local per parameter the prototype
  declares, of the declared type, and passes them by name. A value the
  scaffold chose is no longer frozen into author text. That the example
  still compiles against its header is gh-1487's matrix
  (``test_gh1487_header_code_example.py``), which carries an every-kind shape
  for this issue.
* **Report.** The example stays the author's text, so jm does not rewrite
  it. ``jm status`` names an example whose argument count no longer matches
  the prototype, as advice: ``--check`` does not fail on it, because a
  gating finding on author text would be clearable only by retyping jm's.
"""

from __future__ import annotations

from just_makeit import _incpath as INC  # noqa: E402
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _jmrun import run_cli  # noqa: E402
from test_gh1487_header_code_example import _example, _header  # noqa: E402

_KINDS = [
    "--init-param",
    "n:size_t:16",
    "--init-param",
    "name:const char *",
    "--init-param",
    "mode:string_enum:a,b",
    "--init-param",
    "h:float[]",
]


def _new(base: Path) -> Path:
    r = run_cli("new", "p", cwd=base)
    assert r.returncode == 0, r.stderr
    return base / "p"


def _prototype(h: str, name: str) -> list[tuple[str, str]]:
    """``(type, name)`` per parameter, as the header's declaration has it."""
    m = re.search(
        rf"^{name}_state_t \*{name}_create\((.*?)\);", h, re.M | re.S
    )
    assert m, "no create() declaration"
    out = []
    for p in m.group(1).split(","):
        pm = re.fullmatch(r"\s*(.*?)\s*(\w+)\s*", p)
        out.append((re.sub(r"\s*\*$", " *", pm.group(1)), pm.group(2)))
    return out


def test_example_passes_declared_names_as_typed_locals(tmp_path):
    root = _new(tmp_path)
    r = run_cli("object", "o", *_KINDS, cwd=root)
    assert r.returncode == 0, r.stderr
    h = _header(root, "o")
    lines = _example(h)
    proto = _prototype(h, "o")
    # One local per declared parameter, in order, of the declared type.
    locals_ = []
    for ln in lines:
        m = re.match(r"(.*?)\s*\b(\w+) = \S+; // your value$", ln)
        if m:
            locals_.append((re.sub(r"\s*\*$", " *", m.group(1)), m.group(2)))
    assert locals_ == proto, "\n".join(lines)
    call = next(ln for ln in lines if "o_create(" in ln)
    assert call == (
        f"o_state_t *obj = o_create({', '.join(n for _, n in proto)});"
    ), call
    # The value the scaffold was given is not frozen into the example.
    assert "16" not in "\n".join(lines)


def test_arity_drift_is_reported_and_does_not_gate(tmp_path):
    root = _new(tmp_path)
    r = run_cli(
        "object", "o", "--no-step", "--state", "threshold:int:20", cwd=root
    )
    assert r.returncode == 0, r.stderr
    frag = root / "objects" / "o.toml"

    def add(name: str, ctype: str, dflt: str) -> None:
        frag.write_text(
            frag.read_text(encoding="utf-8")
            + f'\n[[o.init_params]]\nname = "{name}"\ntype = "{ctype}"\n'
            f'default = "{dflt}"\n',
            encoding="utf-8",
        )
        r = run_cli("apply", cwd=root)
        assert r.returncode == 0, r.stderr

    # A fresh scaffold's example agrees with its prototype: nothing to say.
    r = run_cli("status", "--check", cwd=root)
    assert r.returncode == 0, r.stdout
    assert "EXAMPLE" not in r.stdout, r.stdout

    add("level", "int", "3")
    add("gain", "float", "1.0f")
    h = _header(root, "o")
    assert "o_create(int level, float gain);" in h
    line = next(
        i for i, ln in enumerate(h.splitlines(), 1) if "obj = o_create(" in ln
    )

    r = run_cli("status", "--check", cwd=root)
    assert r.returncode == 0, r.stdout  # advisory: never gates
    assert "EXAMPLE (1)" in r.stdout, r.stdout
    assert (
        f"{INC.core_rel('o', root)}:{line}  o_create() given 1, declared 2"
        in r.stdout
    ), r.stdout
    assert "Advisory" in r.stdout, r.stdout

    r = run_cli("status", "--json", cwd=root)
    assert json.loads(r.stdout)["create_example_drift"] == [
        {
            "component": "o",
            "path": INC.core_rel("o", root),
            "line": line,
            "passed": 1,
            "declared": 2,
        }
    ]

"""gh-2174: the one-leg gate variants are set by ONE switch, in the Makefile.

Before this, the two gates that run a stricter half on a single CI leg were
set only by ci.yml: the shrink half of Gate A (``JM_GATE_A_SHRINK``) and the
full verb gate (``JM_VERB_GATE_FULL``). ``make test-examples`` and
``make gates`` never set them, so a local run passed on a tree the leg failed.

The rule, held here without a registration list:

- the variables are named ONCE, by ``ONE_LEG_GATE_VARS`` in the Makefile;
- ci.yml sets only ``CI_FULL``, never one of those variables directly;
- the Makefile exports every one of them under ``CI_FULL``;
- ``make gates`` defaults ``CI_FULL`` to 1, so the local gate set is the union.

Each check reads the files it guards, so none of them can pass by naming a
list that is not the one the Makefile actually uses.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = ROOT / "Makefile"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"


def _one_leg_vars() -> list[str]:
    """The variables the Makefile names as one-leg gate variants."""
    m = re.search(
        r"^ONE_LEG_GATE_VARS\s*=\s*(.+)$",
        MAKEFILE.read_text(encoding="utf-8"),
        re.M,
    )
    assert m, "the Makefile names no ONE_LEG_GATE_VARS"
    return m.group(1).split()


def test_the_makefile_names_the_gate_variants_it_exports():
    names = _one_leg_vars()
    assert names, "ONE_LEG_GATE_VARS is empty"
    assert "JM_GATE_A_SHRINK" in names
    assert "JM_VERB_GATE_FULL" in names


def test_ci_yml_sets_no_gate_variant_directly():
    """A workflow that sets one of these itself bypasses CI_FULL, and the
    local `make gates` then reads a different leg set than CI runs."""
    text = CI_YML.read_text(encoding="utf-8")
    for var in _one_leg_vars():
        hits = re.findall(rf"^\s+{var}:", text, re.M)
        assert not hits, f"ci.yml sets {var} directly; set CI_FULL instead"


def test_the_makefile_exports_every_variant_under_ci_full():
    text = MAKEFILE.read_text(encoding="utf-8")
    block = re.search(
        r"^ifeq \(\$\(CI_FULL\),1\)\n(.*?)^endif", text, re.M | re.S
    )
    assert block, "the Makefile has no `ifeq ($(CI_FULL),1)` block"
    body = block.group(1)
    assert "export $(ONE_LEG_GATE_VARS)" in body, body
    for var in _one_leg_vars():
        assert re.search(rf"^{var} = 1$", body, re.M), (
            f"CI_FULL does not set {var}"
        )


def test_make_gates_defaults_the_switch_on():
    text = MAKEFILE.read_text(encoding="utf-8")
    assert re.search(
        r"ifeq \(\$\(filter gates,\$\(MAKECMDGOALS\)\),gates\)\n"
        r"CI_FULL \?= 1\n",
        text,
    ), "`make gates` does not default CI_FULL to 1"


def test_ci_sets_the_switch_on_the_examples_and_test_legs():
    """Both legs the planner marks carry the switch (gh-2626: marked, never
    a literal Python), so CI exports the one-leg variants on those legs."""
    text = CI_YML.read_text(encoding="utf-8")
    assert len(re.findall(r"^\s+CI_FULL:", text, re.M)) == 2, (
        "ci.yml must set CI_FULL on exactly the two marked steps"
    )

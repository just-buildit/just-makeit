"""A no_step object's bench declares `obj` via a void create (gh-181).

The bench template always emits `<comp>_destroy(obj)`. For a no_step object
with no init params the create was left as a TODO comment, so `obj` was
undeclared and the bench failed to compile. Since `create(void)` is callable,
the bench must declare it.
"""

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402

# A module object with only opaque state, a custom void create, and no_step —
# the kitchen_sink `config` shape that hit gh-181: create(void), no init params.
_CFG_FRAGMENT = '''\
[cfg]
arg_type = "void"
return_type = "void"
no_step = "true"

create_impl = """
obj->h = obj;
"""

[[cfg.state]]
name = "h"
type = "void *"
opaque = true
'''


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def test_nostep_void_create_bench_declares_obj(tmp_path):
    dest = tmp_path / "p"
    _silent(new_run, "p", dest, fragments=True)
    _silent(module_run, dest, "m")
    (dest / "objects").mkdir(exist_ok=True)
    (dest / "objects" / "cfg.toml").write_text(_CFG_FRAGMENT, encoding="utf-8")
    mod = dest / "modules" / "m.toml"
    mod.write_text(
        mod.read_text(encoding="utf-8").replace(
            "objects = []", 'objects = ["cfg"]'
        ),
        encoding="utf-8",
    )
    _silent(apply_run, dest)

    bench = (dest / "native/benchmarks/bench_cfg_core.c").read_text("utf-8")
    assert "cfg_state_t *obj = cfg_create();" in bench  # declared
    assert "TODO" not in bench  # not a commented-out stub
    # destroy still references obj, now validly
    assert "cfg_destroy(obj);" in bench

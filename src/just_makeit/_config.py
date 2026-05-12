"""
_config.py — read/write just-makeit.toml project configuration.

Format
------
[project]
name = "my_project"
version = "0.1.0"

[[engine.state]]
name = "rate"
type = "double"
default = "1.0"

[[parser.state]]
name = "depth"
type = "int"
default = "8"
"""

import tomllib
from pathlib import Path

FILENAME = "just-makeit.toml"


def load(root: Path) -> dict:
    path = root / FILENAME
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def save(root: Path, cfg: dict) -> None:
    (root / FILENAME).write_text(_dump(cfg), encoding="utf-8")


def components(cfg: dict) -> list[str]:
    """Return component names — all top-level keys except 'project' and 'module'."""
    return [k for k in cfg if k not in ("project", "module")]


def modules(cfg: dict) -> list[str]:
    """Return names of explicitly defined multi-object modules."""
    return list(cfg.get("module", {}).keys())


def module_objects(cfg: dict, module: str) -> list[str]:
    """Return the object names belonging to the given module."""
    return list(cfg.get("module", {}).get(module, {}).get("objects", []))


def component_module(cfg: dict, component: str) -> str | None:
    """Return the module that owns this component, or None if self-contained."""
    for mod, data in cfg.get("module", {}).items():
        if component in data.get("objects", []):
            return mod
    return None


def scaffold_module(cfg: dict, module: str) -> dict:
    """Add an empty module entry (no objects yet)."""
    cfg.setdefault("module", {})[module] = {"objects": []}
    return cfg


def add_to_module(cfg: dict, module: str, object_name: str) -> dict:
    """Append object_name to an existing module's object list."""
    cfg.setdefault("module", {}).setdefault(module, {}).setdefault(
        "objects", []
    ).append(object_name)
    return cfg


def module_functions(cfg: dict, module: str) -> list[dict]:
    """Return the module-level function entries for module as [{"name":..., "doc":...}, ...]."""
    return list(cfg.get("module", {}).get(module, {}).get("functions", []))


def add_module_function(cfg: dict, module: str, fn: dict) -> dict:
    """Append a function entry to a module's functions list."""
    (
        cfg.setdefault("module", {})
        .setdefault(module, {})
        .setdefault("functions", [])
        .append(fn)
    )
    return cfg


def is_no_state(cfg: dict, component: str) -> bool:
    """Return True if the component was scaffolded with --no-state."""
    return cfg.get(component, {}).get("no_state") == "true"


def is_no_step(cfg: dict, component: str) -> bool:
    """Return True if the component was scaffolded with --no-step."""
    return cfg.get(component, {}).get("no_step") == "true"


def array_args(cfg: dict, component: str) -> list[tuple[str, str]]:
    """Return declared array constructor args for component as [(name, dtype), ...]."""
    return [
        (a["name"], a["dtype"])
        for a in cfg.get(component, {}).get("array_args", [])
    ]


def init_params(cfg: dict, component: str) -> list[tuple[str, str, str]]:
    """Return --init-param entries for component as [(name, type, default), ...]."""
    return [
        (p["name"], p["type"], p["default"])
        for p in cfg.get(component, {}).get("init_params", [])
    ]


def methods(cfg: dict, component: str) -> list[dict]:
    """Return declared extra methods for component (empty list if none)."""
    return list(cfg.get(component, {}).get("methods", []))


def add_method(cfg: dict, component: str, method: dict) -> dict:
    """Append a method entry to the component's methods list."""
    cfg.setdefault(component, {}).setdefault("methods", []).append(method)
    return cfg


def properties(cfg: dict, component: str) -> list[dict]:
    """Return declared Python properties for component (empty list if none)."""
    return list(cfg.get(component, {}).get("properties", []))


def add_property(cfg: dict, component: str, prop: dict) -> dict:
    """Append a property entry to the component's properties list."""
    cfg.setdefault(component, {}).setdefault("properties", []).append(prop)
    return cfg


def state_vars(cfg: dict, component: str) -> list[tuple[str, str, str]]:
    return [
        (s["name"], s["type"], s["default"])
        for s in cfg.get(component, {}).get("state", [])
    ]


def project_name(cfg: dict) -> str:
    return cfg.get("project", {}).get("name", "")


def project_version(cfg: dict) -> str:
    return cfg.get("project", {}).get("version", "0.1.0")


def build_system(cfg: dict) -> str:
    """Return 'cmake' (default) or 'make' (--basic mode)."""
    return cfg.get("project", {}).get("build", "cmake")


def is_perf(cfg: dict) -> bool:
    return cfg.get("project", {}).get("perf") == "true"


def from_new(
    name: str, version: str = "0.1.0", basic: bool = False, perf: bool = False
) -> dict:
    proj: dict = {"name": name, "version": version}
    if basic:
        proj["build"] = "make"
    if perf:
        proj["perf"] = "true"
    return {"project": proj}


def arg_type(cfg: dict, component: str) -> str:
    return cfg.get(component, {}).get("arg_type", "float _Complex")


def return_type(cfg: dict, component: str) -> str:
    return cfg.get(component, {}).get("return_type", "float _Complex")


def add_component(
    cfg: dict,
    component: str,
    vars_: list[tuple[str, str, str]],
    arg_type_: str = "float _Complex",
    return_type_: str | None = None,
    array_args_: list[tuple[str, str]] = (),
    no_state_: bool = False,
    no_step_: bool = False,
    init_params_: list[tuple[str, str, str]] = (),
) -> dict:
    entry: dict = {
        "state": [{"name": n, "type": t, "default": d} for n, t, d in vars_]
    }
    if arg_type_ != "float _Complex":
        entry["arg_type"] = arg_type_
    rt = (return_type_ if return_type_ is not None
          else "void" if arg_type_.endswith("[]") else arg_type_)
    if rt != "float _Complex":
        entry["return_type"] = rt
    if array_args_:
        entry["array_args"] = [
            {"name": n, "dtype": dt} for n, dt in array_args_
        ]
    if no_state_:
        entry["no_state"] = "true"
    if no_step_:
        entry["no_step"] = "true"
    if init_params_:
        entry["init_params"] = [
            {"name": n, "type": t, "default": d} for n, t, d in init_params_
        ]
    cfg[component] = entry
    return cfg


def _dump(cfg: dict) -> str:
    lines: list[str] = []

    proj = cfg.get("project", {})
    if proj:
        lines.append("[project]")
        for k, v in proj.items():
            if k == "build" and v == "cmake":
                continue  # cmake is default, don't write it
            lines.append(f'{k} = "{v}"')
        lines.append("")

    for mod, data in cfg.get("module", {}).items():
        lines.append(f"[module.{mod}]")
        objs = data.get("objects", [])
        objs_str = ", ".join(f'"{o}"' for o in objs)
        lines.append(f"objects = [{objs_str}]")
        lines.append("")
        for fn in data.get("functions", []):
            lines.append(f"[[module.{mod}.functions]]")
            lines.append(f'name = "{fn["name"]}"')
            if fn.get("doc"):
                lines.append(f'doc = "{fn["doc"]}"')
            if fn.get("return_type"):
                lines.append(f'return_type = "{fn["return_type"]}"')
            if fn.get("params"):
                parts = ", ".join(
                    f'{{name = "{p["name"]}", type = "{p["type"]}"}}'
                    for p in fn["params"]
                )
                lines.append(f"params = [{parts}]")
            lines.append("")

    for comp in components(cfg):
        comp_data = cfg[comp]
        meta_keys = [k for k in ("arg_type", "return_type", "no_state", "no_step")
                     if comp_data.get(k)]
        if meta_keys:
            lines.append(f"[{comp}]")
            for k in meta_keys:
                lines.append(f'{k} = "{comp_data[k]}"')
            lines.append("")
        for a in comp_data.get("array_args", []):
            lines.append(f"[[{comp}.array_args]]")
            lines.append(f'name = "{a["name"]}"')
            lines.append(f'dtype = "{a["dtype"]}"')
            lines.append("")
        for s in comp_data.get("state", []):
            lines.append(f"[[{comp}.state]]")
            lines.append(f'name = "{s["name"]}"')
            lines.append(f'type = "{s["type"]}"')
            lines.append(f'default = "{s["default"]}"')
            lines.append("")
        for p in comp_data.get("init_params", []):
            lines.append(f"[[{comp}.init_params]]")
            lines.append(f'name = "{p["name"]}"')
            lines.append(f'type = "{p["type"]}"')
            lines.append(f'default = "{p["default"]}"')
            lines.append("")
        for m in comp_data.get("methods", []):
            lines.append(f"[[{comp}.methods]]")
            lines.append(f'name = "{m["name"]}"')
            if m.get("arg_type"):
                lines.append(f'arg_type = "{m["arg_type"]}"')
            if m.get("return_type"):
                lines.append(f'return_type = "{m["return_type"]}"')
            if m.get("variable_output"):
                lines.append("variable_output = true")
            if m.get("multi_output"):
                mo_str = ", ".join(f'"{t}"' for t in m["multi_output"])
                lines.append(f"multi_output = [{mo_str}]")
            if m.get("params"):
                parts = ", ".join(
                    f'{{name = "{p["name"]}", type = "{p["type"]}"}}'
                    for p in m["params"]
                )
                lines.append(f"params = [{parts}]")
            if m.get("out_type"):
                lines.append(f'out_type = "{m["out_type"]}"')
            if m.get("out_divisor") and m["out_divisor"] != 1:
                lines.append(f'out_divisor = {m["out_divisor"]}')
            lines.append("")
        for p in comp_data.get("properties", []):
            lines.append(f"[[{comp}.properties]]")
            lines.append(f'name = "{p["name"]}"')
            lines.append(f'ctype = "{p["ctype"]}"')
            if p.get("writable"):
                lines.append("writable = true")
            if p.get("field"):
                lines.append("field = true")
            lines.append("")

    return "\n".join(lines)

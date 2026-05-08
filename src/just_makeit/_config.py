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
    """Return component names — all top-level keys except 'project'."""
    return [k for k in cfg if k != "project"]


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


def pure_style(cfg: dict, component: str) -> str | None:
    """Return 'scalar', 'struct', or None (stateful / not pure)."""
    v = cfg.get(component, {}).get("pure")
    return v if v in ("scalar", "struct") else None


def is_pure_component(cfg: dict, component: str) -> bool:
    return pure_style(cfg, component) is not None


def add_component(
    cfg: dict,
    component: str,
    vars_: list[tuple[str, str, str]],
    pure: str | None = None,
) -> dict:
    entry: dict = {
        "state": [{"name": n, "type": t, "default": d} for n, t, d in vars_]
    }
    if pure:
        entry["pure"] = pure
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

    for comp in components(cfg):
        comp_data = cfg[comp]
        if comp_data.get("pure"):
            lines.append(f"[{comp}]")
            lines.append(f'pure = "{comp_data["pure"]}"')
            lines.append("")
        for s in comp_data.get("state", []):
            lines.append(f"[[{comp}.state]]")
            lines.append(f'name = "{s["name"]}"')
            lines.append(f'type = "{s["type"]}"')
            lines.append(f'default = "{s["default"]}"')
            lines.append("")

    return "\n".join(lines)

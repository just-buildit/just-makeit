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


def from_new(name: str, version: str = "0.1.0", basic: bool = False) -> dict:
    proj: dict = {"name": name, "version": version}
    if basic:
        proj["build"] = "make"
    return {"project": proj}


def add_component(
    cfg: dict, component: str, vars_: list[tuple[str, str, str]]
) -> dict:
    cfg[component] = {
        "state": [{"name": n, "type": t, "default": d} for n, t, d in vars_]
    }
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
        for s in cfg[comp].get("state", []):
            lines.append(f"[[{comp}.state]]")
            lines.append(f'name = "{s["name"]}"')
            lines.append(f'type = "{s["type"]}"')
            lines.append(f'default = "{s["default"]}"')
            lines.append("")

    return "\n".join(lines)

"""
_config.py — read/write just-makeit.toml project configuration.

Format
------
[component]
name = "my_filter"
version = "0.1.0"

[[state]]
name = "gain"
type = "double"
default = "1.0"
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


def state_vars(cfg: dict) -> list[tuple[str, str, str]]:
    return [(s["name"], s["type"], s["default"]) for s in cfg.get("state", [])]


def from_init(
    component: str,
    version: str,
    vars_: list[tuple[str, str, str]],
) -> dict:
    return {
        "component": {"name": component, "version": version},
        "state": [{"name": n, "type": t, "default": d} for n, t, d in vars_],
    }


def _dump(cfg: dict) -> str:
    lines: list[str] = []

    comp = cfg.get("component", {})
    if comp:
        lines.append("[component]")
        for k, v in comp.items():
            lines.append(f'{k} = "{v}"')
        lines.append("")

    for s in cfg.get("state", []):
        lines.append("[[state]]")
        lines.append(f'name = "{s["name"]}"')
        lines.append(f'type = "{s["type"]}"')
        lines.append(f'default = "{s["default"]}"')
        lines.append("")

    return "\n".join(lines)

"""
_script.py — `just-makeit script` command.

Reads just-makeit.toml in the current directory and emits a shell script
that would reconstruct the project from scratch via the CLI.

Note: --impl / --replace are not stored in TOML (the lifted body is patched
directly into the generated files), so they are not reproduced here.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import _config as C


def _q(s: str) -> str:
    """Quote a CLI value if it contains spaces or special characters.

    >>> _q("float")
    'float'
    >>> _q("float _Complex")
    '"float _Complex"'
    >>> _q("float _Complex[]")
    '"float _Complex[]"'
    >>> _q("fir_create_poly(d0,d1,ptr)")
    '"fir_create_poly(d0,d1,ptr)"'
    """
    if " " in s or "(" in s or ")" in s or "[" in s:
        return f'"{s}"'
    return s


def _flag(name: str, val: str) -> str:
    """Format a single CLI flag line.

    >>> _flag("--arg-type", "float")
    '    --arg-type float \\\\\\n'
    >>> _flag("--arg-type", "float _Complex")
    '    --arg-type "float _Complex" \\\\\\n'
    """
    return f"    {name} {_q(val)} \\\n"


def _bool_flag(name: str) -> str:
    """Format a boolean CLI flag line.

    >>> _bool_flag("--mutable")
    '    --mutable \\\\\\n'
    """
    return f"    {name} \\\n"


def _object_flags(
    cfg: dict, comp: str, module: str | None = None
) -> list[str]:
    """Return the CLI flag lines for a single object."""
    parts: list[str] = []

    if module:
        parts.append(_flag("--module", module))

    for name, dtype in C.array_args(cfg, comp):
        parts.append(_flag("--array-arg", f"{name}:{dtype}"))

    for name, typ, default in C.state_vars(cfg, comp):
        val = f"{name}:{typ}:{default}" if default else f"{name}:{typ}"
        parts.append(_flag("--state", val))

    for name, typ, default, *_ in C.init_params(cfg, comp):
        val = f"{name}:{typ}:{default}" if default else f"{name}:{typ}"
        parts.append(_flag("--init-param", val))

    at = C.arg_type(cfg, comp)
    if at != "float _Complex":
        parts.append(_flag("--arg-type", at))

    # Only omit --return-type when it matches the CLI default for this arg-type:
    #   array arg  → void;  any other → same as arg-type.
    rt = C.return_type(cfg, comp)
    implicit_rt = "void" if at.endswith("[]") else at
    if rt != implicit_rt:
        parts.append(_flag("--return-type", rt))

    if C.is_perf(cfg):
        parts.append(_bool_flag("--perf"))

    if C.is_mutable(cfg, comp):
        parts.append(_bool_flag("--mutable"))

    if C.is_no_state(cfg, comp):
        parts.append(_bool_flag("--no-state"))

    if C.is_no_step(cfg, comp):
        parts.append(_bool_flag("--no-step"))

    if C.step_delegates(cfg, comp):
        parts.append(_bool_flag("--step-delegates-to-steps"))

    if C.is_streamable(cfg, comp):
        # --async-stream implies --streamable; emit the most specific flag.
        # --stream-block also implies --streamable, so prefer it when a
        # non-default block was recorded.
        if C.is_async_stream(cfg, comp):
            parts.append(_bool_flag("--async-stream"))
        if "stream_block_default" in cfg.get(comp, {}):
            parts.append(
                _flag("--stream-block", str(C.stream_block_default(cfg, comp)))
            )
        elif not C.is_async_stream(cfg, comp):
            parts.append(_bool_flag("--streamable"))

    return parts


def _method_flags(m: dict, module: str | None) -> list[str]:
    parts: list[str] = []

    if module:
        parts.append(_flag("--module", module))

    for p in m.get("params", []):
        val = f"{p['name']}:{p['type']}"
        parts.append(_flag("--param", val))

    at = m.get("arg_type", "")
    if at:
        parts.append(_flag("--arg-type", at))

    rt = m.get("return_type", "")
    if rt:
        parts.append(_flag("--return-type", rt))

    if m.get("varargs"):
        parts.append(_bool_flag("--varargs"))

    if m.get("batch"):
        parts.append(_bool_flag("--batch"))

    if m.get("variable_output"):
        parts.append(_bool_flag("--variable-output"))
    if m.get("pass_capacity"):
        parts.append(_bool_flag("--pass-capacity"))

    for mo in m.get("multi_output", []):
        parts.append(_flag("--multi-output", mo))

    if m.get("out_type"):
        parts.append(_flag("--out-type", m["out_type"]))

    if m.get("out_divisor") and m["out_divisor"] != 1:
        parts.append(_flag("--out-divisor", str(m["out_divisor"])))

    return parts


def _property_flags(p: dict, module: str | None) -> list[str]:
    parts: list[str] = []

    if module:
        parts.append(_flag("--module", module))

    parts.append(_flag("--type", p.get("type") or p.get("ctype", "size_t")))

    if p.get("writable"):
        parts.append(_bool_flag("--writable"))

    if p.get("field"):
        parts.append(_bool_flag("--field"))

    return parts


def _function_flags(fn: dict, module: str) -> list[str]:
    parts: list[str] = [_flag("--module", module)]

    for p in fn.get("params", []):
        val = f"{p['name']}:{p['type']}"
        parts.append(_flag("--param", val))

    rt = fn.get("return_type", "")
    if rt:
        parts.append(_flag("--return-type", rt))

    if fn.get("doc"):
        parts.append(_flag("--doc", fn["doc"]))

    return parts


def _render_cmd(cmd_parts: list[str], flag_lines: list[str]) -> str:
    """Combine a command head with continuation flag lines."""
    head = " ".join(cmd_parts)
    if not flag_lines:
        return head + "\n"
    # Strip trailing backslash-newline from last flag line
    all_flags = flag_lines[:-1] + [flag_lines[-1].rstrip(" \\\n") + "\n"]
    return head + " \\\n" + "".join(all_flags)


def run(root: Path) -> None:
    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\nRun 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = C.load(root)
    project = C.project_name(cfg)
    version = C.project_version(cfg)
    bs = C.build_system(cfg)
    perf = C.is_perf(cfg)
    mods = C.modules(cfg)
    module_owned = {obj for mod in mods for obj in C.module_objects(cfg, mod)}
    standalone = [c for c in C.components(cfg) if c not in module_owned]

    lines: list[str] = [
        "#!/usr/bin/env sh\n",
        f"# Reconstructed from {C.FILENAME}\n\n",
    ]

    # ── new ──────────────────────────────────────────────────────────────────
    new_flags: list[str] = []
    if bs == "make":
        new_flags.append("--build-system make")
    if perf:
        new_flags.append(_bool_flag("--perf"))
    if C.is_pytest(cfg):
        new_flags.append(_bool_flag("--pytest"))
    if C.is_pytest_benchmark(cfg):
        new_flags.append(_bool_flag("--pytest-benchmark"))
    lines.append(_render_cmd(["just-makeit", "new", project], new_flags))
    lines.append(f"cd {project}\n\n")

    if version != "0.1.0":
        lines.append(f"just-makeit config version {version}\n\n")

    # ── modules ──────────────────────────────────────────────────────────────
    for mod in mods:
        lines.append(_render_cmd(["just-makeit", "module", mod], []))

    if mods:
        lines.append("\n")

    # ── standalone objects ───────────────────────────────────────────────────
    for comp in standalone:
        flags = _object_flags(cfg, comp)
        lines.append(_render_cmd(["just-makeit", "object", comp], flags))

    if standalone:
        lines.append("\n")

    # ── module objects ────────────────────────────────────────────────────────
    for mod in mods:
        for comp in C.module_objects(cfg, mod):
            flags = _object_flags(cfg, comp, module=mod)
            lines.append(_render_cmd(["just-makeit", "object", comp], flags))
        lines.append("\n")

    # ── methods ───────────────────────────────────────────────────────────────
    all_comps = list(standalone)
    for mod in mods:
        all_comps += C.module_objects(cfg, mod)

    method_lines: list[str] = []
    for comp in all_comps:
        mod = C.component_module(cfg, comp)
        for m in C.methods(cfg, comp):
            flags = _method_flags(m, mod)
            method_lines.append(
                _render_cmd(["just-makeit", "method", comp, m["name"]], flags)
            )
    if method_lines:
        lines += method_lines
        lines.append("\n")

    # ── properties ────────────────────────────────────────────────────────────
    prop_lines: list[str] = []
    for comp in all_comps:
        mod = C.component_module(cfg, comp)
        for p in C.properties(cfg, comp):
            flags = _property_flags(p, mod)
            prop_lines.append(
                _render_cmd(
                    ["just-makeit", "property", comp, p["name"]], flags
                )
            )
    if prop_lines:
        lines += prop_lines
        lines.append("\n")

    # ── module-level functions ─────────────────────────────────────────────────
    fn_lines: list[str] = []
    for mod in mods:
        for fn in C.module_functions(cfg, mod):
            flags = _function_flags(fn, mod)
            fn_lines.append(
                _render_cmd(["just-makeit", "function", fn["name"]], flags)
            )
    if fn_lines:
        lines += fn_lines
        lines.append("\n")

    sys.stdout.write("".join(lines))

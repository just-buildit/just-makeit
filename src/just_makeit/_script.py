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

    if C.is_no_reset(cfg, comp):
        parts.append(_bool_flag("--no-reset"))

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

    # gh-509: object-level C constructor override.
    cf = C.object_create_fn(cfg, comp)
    if cf:
        parts.append(_flag("--create-fn", cf))

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
    if m.get("count_default"):
        parts.append(f'--count-default "{m["count_default"]}"')
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
    """CLI flags reconstructing a ``[[<comp>.properties]]`` entry.

    Emits every key the manifest can hold. It used to drop ``doc``, ``expr``,
    ``buf_field``, ``len_field`` and ``valid_field`` (gh-490), so `jm script`
    silently produced a script that rebuilt a *different* project — a
    buf-backed ndarray property came back as a plain scalar getter, and an
    expr-backed one lost its expression entirely. A reconstruction that
    quietly differs from the original is worse than one that fails loudly.

    >>> _property_flags({"name": "n", "type": "size_t"}, None)
    ['    --type size_t \\\\\\n']
    >>> _property_flags({"name": "buf", "type": "float[]",
    ...                  "buf_field": "data", "len_field": "n"}, None)[1:]
    ['    --buf-field data \\\\\\n', '    --len-field n \\\\\\n']
    >>> _property_flags({"name": "kw", "type": "dict",
    ...                  "value_type": "object",
    ...                  "count_fn": "r_nkw"}, None)[1:]
    ['    --value-type object \\\\\\n', '    --count-fn r_nkw \\\\\\n']
    """
    parts: list[str] = []

    if module:
        parts.append(_flag("--module", module))

    parts.append(_flag("--type", p.get("type") or p.get("ctype", "size_t")))

    if p.get("writable"):
        parts.append(_bool_flag("--writable"))

    if p.get("field"):
        parts.append(_bool_flag("--field"))

    if p.get("buf_field"):
        parts.append(_flag("--buf-field", p["buf_field"]))
        # len_field only means anything alongside buf_field, and defaults to
        # "n" — emit it explicitly so a non-default survives the round-trip.
        parts.append(_flag("--len-field", p.get("len_field", "n")))

    if p.get("valid_field"):
        parts.append(_flag("--valid-field", p["valid_field"]))

    if p.get("expr"):
        parts.append(_flag("--expr", p["expr"]))

    # gh-519: the [[enum]] the property decodes through. Dropping it would
    # rebuild the property as a bare int — the exact silent-divergence the
    # gh-490 note above warns about.
    if p.get("enum"):
        parts.append(_flag("--enum", p["enum"]))

    # gh-543: a container property's accessors. Only what the manifest
    # actually records is emitted -- an unspecified accessor is re-derived
    # from the same default on replay, so omitting it round-trips exactly.
    if p.get("value_type"):
        parts.append(_flag("--value-type", p["value_type"]))
    for _fn, _flagname in (
        ("count_fn", "--count-fn"),
        ("key_fn", "--key-fn"),
        ("value_fn", "--value-fn"),
    ):
        if p.get(_fn):
            parts.append(_flag(_flagname, p[_fn]))

    if p.get("doc"):
        parts.append(_flag("--doc", p["doc"]))

    return parts


def _view_flags(v: dict, module: str | None) -> list[str]:
    """CLI flags reconstructing a ``[[<comp>.views]]`` entry (gh-504).

    Every key the manifest can hold is emitted, so `jm script` round-trips a
    view faithfully (the gh-490 lesson: a reconstruction that silently differs
    from the original is worse than one that fails loudly). The view's class
    name is a positional on the command, not a flag, so it is not emitted here.

    >>> _view_flags({"class_name": "Burst", "create_fn": "acq_create_burst",
    ...              "init_params": [{"name": "reps", "type": "int"}],
    ...              "exclude_properties": ["symbol_rate"],
    ...              "exclude_methods": ["tune"]}, "dsp")
    ['    --module dsp \\\\\\n', '    --create-fn acq_create_burst \\\\\\n', \
'    --init-param reps:int \\\\\\n', '    --exclude-property symbol_rate \\\\\\n', \
'    --exclude-method tune \\\\\\n']
    """
    parts: list[str] = []

    if module:
        parts.append(_flag("--module", module))

    parts.append(_flag("--create-fn", v["create_fn"]))

    for p in v.get("init_params", []):
        name, typ = p["name"], p["type"]
        if p.get("optional"):
            spec = f"{name}:{typ}:optional"
            if p.get("create_fn"):
                spec += f":{p['create_fn']}"
        elif p.get("required"):
            spec = f"{name}:{typ}:required"
        elif p.get("default") not in (None, ""):
            spec = f"{name}:{typ}:{p['default']}"
        else:
            spec = f"{name}:{typ}"
        parts.append(_flag("--init-param", spec))

    for name in v.get("exclude_properties", []):
        parts.append(_flag("--exclude-property", name))

    for name in v.get("exclude_methods", []):
        parts.append(_flag("--exclude-method", name))

    if v.get("doc"):
        parts.append(_flag("--doc", v["doc"]))

    return parts


def _warning_flags(w: dict, module: str | None) -> list[str]:
    """CLI flags reconstructing a ``[[<comp>.warnings]]`` entry (gh-481).

    Every key the manifest can hold is emitted. A `jm script` that quietly
    lost the warning text would recreate the very bug this feature fixes, one
    layer up — the reconstruction would rebuild a *different* project without
    saying so. (`_property_flags` did exactly that until gh-490.)

    >>> _warning_flags({"condition": "underpowered", "message": "best effort",
    ...                 "category": "UserWarning"}, None)
    ['    --condition underpowered \\\\\\n', '    --message "best effort" \\\\\\n']
    """
    parts: list[str] = []

    if module:
        parts.append(_flag("--module", module))

    parts.append(_flag("--condition", w["condition"]))
    parts.append(_flag("--message", w["message"]))

    # Defaults are omitted so the reconstructed script reads like one a human
    # would have typed, but anything non-default must survive.
    if w.get("category", "UserWarning") != "UserWarning":
        parts.append(_flag("--category", w["category"]))

    if w.get("after", "__init__") != "__init__":
        parts.append(_flag("--after", w["after"]))

    if int(w.get("stacklevel", 1) or 1) != 1:
        parts.append(_flag("--stacklevel", str(w["stacklevel"])))

    return parts


def _function_flags(fn: dict, module: str) -> list[str]:
    parts: list[str] = [_flag("--module", module)]

    for p in fn.get("params", []):
        # gh-353: reconstruct the path / enum --param syntax.
        #   path arg  -> name:path
        #   enum arg  -> name:enum:<ename>[=<default>]   (type is "int")
        # A plain scalar with a default round-trips as name:type=<default>.
        if p["type"] == "path":
            val = f"{p['name']}:path"
        elif p.get("enum"):
            val = f"{p['name']}:enum:{p['enum']}"
            if p.get("default") not in (None, ""):
                val += f"={p['default']}"
        else:
            val = f"{p['name']}:{p['type']}"
            if p.get("default") not in (None, ""):
                val += f"={p['default']}"
        parts.append(_flag("--param", val))

    rt = fn.get("return_type", "")
    if rt:
        parts.append(_flag("--return-type", rt))

    # gh-318/gh-335: a self-sizing output. Emit these so the reconstructed
    # command regenerates the out-last / out_size-sized binding rather than the
    # plain-out_type path (which would under-allocate).
    if fn.get("out_type"):
        parts.append(_flag("--out-type", fn["out_type"]))
    if fn.get("variable_output"):
        parts.append(_bool_flag("--variable-output"))
    if fn.get("out_size"):
        parts.append(_flag("--out-size", fn["out_size"]))
    if fn.get("check_return"):
        parts.append(_bool_flag("--check-return"))

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
        # gh-523: `package` has no CLI flag on any module kind — it is a
        # manifest-only key. Emitting the bare `jm module` command would
        # silently rebuild the module in a package of its own (the gh-490
        # silent-divergence trap), so flag it for the reader instead.
        pkg_override = C.module_package(cfg, mod)
        if pkg_override:
            lines.append(
                f'# NOTE: [module.{mod}] package = "{pkg_override}" has no\n'
                f"# CLI flag — re-add it to just-makeit.toml and run"
                f" `just-makeit apply`.\n"
            )
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

    # ── views ─────────────────────────────────────────────────────────────────
    # After properties: a view's --exclude-property names properties that must
    # already be declared for the reconstruction to make sense.
    view_lines: list[str] = []
    for comp in all_comps:
        mod = C.component_module(cfg, comp)
        for v in C.views(cfg, comp):
            view_lines.append(
                _render_cmd(
                    ["just-makeit", "view", comp, v["class_name"]],
                    _view_flags(v, mod),
                )
            )
            # gh-504: the view's OWN added/overriding members, after its
            # `jm view` line (methods before properties, matching apply replay).
            cls = v["class_name"]
            for m in C.view_methods(v):
                view_lines.append(
                    _render_cmd(
                        ["just-makeit", "method", comp, m["name"]],
                        _method_flags(m, mod) + [_flag("--view", cls)],
                    )
                )
            for p in C.view_properties(v):
                view_lines.append(
                    _render_cmd(
                        ["just-makeit", "property", comp, p["name"]],
                        _property_flags(p, mod) + [_flag("--view", cls)],
                    )
                )
            # gh-509: the view's OWN warnings, reusing the object-warning flag
            # builder plus --view (mirrors the members above).
            for w in C.view_warnings(v):
                view_lines.append(
                    _render_cmd(
                        ["just-makeit", "warning", comp],
                        _warning_flags(w, mod) + [_flag("--view", cls)],
                    )
                )
            # gh-580: the view's OWN create_error, if it declared one. Read the
            # raw key rather than C.view_create_error() — the accessor resolves
            # the parent's value, and re-emitting an inherited translation as an
            # explicit `jm error --view` would turn inheritance into a frozen
            # copy on every round-trip.
            if v.get("create_error"):
                err_flags = []
                if mod:
                    err_flags.append(_flag("--module", mod))
                err_flags.append(_flag("--category", v["create_error"]))
                err_flags.append(
                    _flag("--message", v.get("create_error_message", ""))
                )
                view_lines.append(
                    _render_cmd(
                        ["just-makeit", "error", comp],
                        err_flags + [_flag("--view", cls)],
                    )
                )
    if view_lines:
        lines += view_lines
        lines.append("\n")

    # ── warnings ──────────────────────────────────────────────────────────────
    # After properties: a warning's condition is often a field-backed property,
    # and the reconstructed script should declare the field before referencing
    # it — jm warns on an unknown condition.
    warn_lines: list[str] = []
    for comp in all_comps:
        mod = C.component_module(cfg, comp)
        for w in C.warnings(cfg, comp):
            warn_lines.append(
                _render_cmd(
                    ["just-makeit", "warning", comp], _warning_flags(w, mod)
                )
            )
    if warn_lines:
        lines += warn_lines
        lines.append("\n")

    # ── create() error translation ────────────────────────────────────────────
    err_lines: list[str] = []
    for comp in all_comps:
        mod = C.component_module(cfg, comp)
        cat = C.create_error(cfg, comp)
        if not cat:
            continue
        flags: list[str] = []
        if mod:
            flags.append(_flag("--module", mod))
        flags.append(_flag("--category", cat))
        flags.append(_flag("--message", C.create_error_message(cfg, comp)))
        err_lines.append(_render_cmd(["just-makeit", "error", comp], flags))
    if err_lines:
        lines += err_lines
        lines.append("\n")

    # ── destructor contract (gh-541/gh-544) ───────────────────────────────────
    # Manifest-only, exactly like `package` above: five interacting keys is not
    # a CLI shape. Emitting nothing would be the gh-490 silent-divergence trap
    # (the replayed script would quietly produce a void `destroy()` again), so
    # the reader is told to carry the table over.
    destroy_lines: list[str] = []
    for comp in all_comps:
        if not C.destroy_spec(cfg, comp):
            continue
        destroy_lines.append(
            f"# NOTE: [{comp}.destroy] has no CLI flag — copy the table into\n"
            f"# just-makeit.toml and run `just-makeit apply`.\n"
        )
    if destroy_lines:
        lines += destroy_lines
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

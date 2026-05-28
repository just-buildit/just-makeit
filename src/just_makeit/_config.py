"""
_config.py — read/write just-makeit.toml project configuration.

Format
------
[project]
name = "my_project"
version = "0.1.0"

[[engine.state]]
name = "rate"
type = "float"
default = "1.0f"

[[parser.state]]
name = "depth"
type = "int32_t"
default = "8"
"""

import tomllib
from pathlib import Path

FILENAME = "just-makeit.toml"

# Increment this whenever a new migration is added to _upgrade.py.
CURRENT_SCHEMA = 6


def _resolve_includes(root: Path, includes: list[str]) -> list[Path]:
    """Expand `include` entries (globs and explicit paths, relative to root)
    into a de-duplicated, sorted list of fragment files."""
    seen: set[Path] = set()
    out: list[Path] = []
    for entry in includes:
        if any(c in entry for c in "*?["):
            matches = sorted(root.glob(entry))
        else:
            matches = [root / entry]
            if not matches[0].exists():
                raise FileNotFoundError(
                    f"include not found: {entry} (relative to {root})"
                )
        for p in matches:
            if p not in seen:
                seen.add(p)
                out.append(p)
    return out


def _merge_fragment(cfg: dict, fragment: dict, source: Path) -> None:
    """Merge an included fragment into *cfg*. Top-level object sections
    are added; only `[[module.X.functions]]` extensions are permitted under
    `[module]` — the `[module.X]` declaration itself belongs in the
    manifest."""
    for key, value in fragment.items():
        if key == "project":
            raise ValueError(
                f"{source}: [project] must live in the manifest, "
                f"not in an included fragment."
            )
        if key == "module":
            for mod, mod_data in (value or {}).items():
                if not isinstance(mod_data, dict):
                    continue
                fns = mod_data.get("functions")
                if fns:
                    (
                        cfg.setdefault("module", {})
                        .setdefault(mod, {})
                        .setdefault("functions", [])
                        .extend(fns)
                    )
                other = set(mod_data) - {"functions"}
                if other:
                    raise ValueError(
                        f"{source}: only [[module.{mod}.functions]] may be "
                        f"declared in a fragment; [module.{mod}] belongs in "
                        f"the manifest."
                    )
            continue
        if key in cfg:
            raise ValueError(
                f"{source}: '{key}' already exists. "
                f"Run `jm remove object {key}` first, "
                f"or rename the object in the fragment."
            )
        cfg[key] = value


def load_manifest(root: Path) -> dict:
    """Read the manifest without resolving `include`. Use this when you
    need to inspect or modify the manifest itself; consumers wanting the
    merged project should call `load`."""
    path = root / FILENAME
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def load(root: Path) -> dict:
    """Read the manifest and merge every included fragment into one dict
    (schema 6+). For a single-file project (no `include` key) the result
    is identical to `tomllib.load` of the manifest — full backward
    compatibility."""
    cfg = load_manifest(root)
    includes = cfg.pop("include", None)
    if includes:
        for fragment_path in _resolve_includes(root, includes):
            with fragment_path.open("rb") as f:
                fragment = tomllib.load(f)
            _merge_fragment(cfg, fragment, fragment_path)
    return cfg


def _toml_string_array(items: list[str]) -> str:
    """Render a list of strings as a TOML inline array (double quotes)."""
    import json

    return "[" + ", ".join(json.dumps(s) for s in items) + "]"


def _provenance(root: Path) -> tuple[dict[str, Path], list[str]]:
    """Re-derive which file each top-level object section currently lives in.

    Returns (owners, include_list) where owners[key] is the Path of the
    file that owns *key* on disk, and include_list is the manifest's
    `include` list (empty for single-file projects)."""
    owners: dict[str, Path] = {}
    manifest_path = root / FILENAME
    if not manifest_path.exists():
        return owners, []
    with manifest_path.open("rb") as f:
        manifest = tomllib.load(f)
    for k in manifest:
        if k not in ("project", "module", "include"):
            owners[k] = manifest_path
    include_list = list(manifest.get("include", []))
    for fragment_path in _resolve_includes(root, include_list):
        with fragment_path.open("rb") as f:
            fragment = tomllib.load(f)
        for k in fragment:
            if k not in ("project", "module", "include"):
                owners[k] = fragment_path
    return owners, include_list


def save(root: Path, cfg: dict) -> None:
    """Write cfg back to disk, routing each top-level object section to
    the file that owns it on disk. `[project]` / `[module.X]` always
    live in the manifest. New objects go to `objects/<name>.toml` when
    the project uses the split layout, or to the manifest otherwise.
    A fragment file that ends up with no sections is deleted."""
    manifest_path = root / FILENAME
    owners, include_list = _provenance(root)
    split_layout = bool(include_list)

    # Group every top-level object section in cfg by destination file.
    by_file: dict[Path, dict] = {}
    objects_in_cfg: set[str] = set()
    for key, value in cfg.items():
        if key in ("project", "module", "include"):
            continue
        objects_in_cfg.add(key)
        if key in owners:
            dst = owners[key]
        elif split_layout:
            dst = root / "objects" / f"{key}.toml"
        else:
            dst = manifest_path
        by_file.setdefault(dst, {})[key] = value

    # Manifest always carries [project] / [module.X] / include + any
    # object sections that route to it.
    manifest_content: dict = {}
    if "project" in cfg:
        manifest_content["project"] = cfg["project"]
    if "module" in cfg:
        manifest_content["module"] = cfg["module"]
    manifest_content.update(by_file.get(manifest_path, {}))

    manifest_text = _dump(manifest_content)
    if include_list:
        manifest_text = (
            f"include = {_toml_string_array(include_list)}\n\n" + manifest_text
        )
    manifest_path.write_text(manifest_text, encoding="utf-8")

    # Each fragment file gets only its remaining sections; an empty
    # fragment is removed.
    seen_fragments = {fp for fp in owners.values() if fp != manifest_path}
    for fragment_path in seen_fragments:
        sections = by_file.get(fragment_path, {})
        if sections:
            fragment_path.parent.mkdir(parents=True, exist_ok=True)
            fragment_path.write_text(_dump(sections), encoding="utf-8")
        else:
            fragment_path.unlink(missing_ok=True)

    # Brand-new fragment files (new object in a split project).
    for fragment_path, sections in by_file.items():
        if fragment_path == manifest_path:
            continue
        if fragment_path in seen_fragments:
            continue
        fragment_path.parent.mkdir(parents=True, exist_ok=True)
        fragment_path.write_text(_dump(sections), encoding="utf-8")


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


def is_mutable(cfg: dict, component: str) -> bool:
    """Return True if the component was scaffolded with --mutable."""
    return cfg.get(component, {}).get("mutable") == "true"


def is_no_state(cfg: dict, component: str) -> bool:
    """Return True if the component was scaffolded with --no-state."""
    return cfg.get(component, {}).get("no_state") == "true"


def is_no_step(cfg: dict, component: str) -> bool:
    """Return True if the component was scaffolded with --no-step."""
    return cfg.get(component, {}).get("no_step") == "true"


def extra_types(cfg: dict, module: str) -> list[str]:
    """Return hand-declared extra Python type names for a module's PyInit_.

    Types listed here have ``PyType_Ready`` and ``PyModule_AddObject`` calls
    generated in the aggregator ``PyInit_<module>`` automatically, so they
    survive every ``jm apply`` / ``jm object`` call without a hand-patch.

    The types themselves must be defined in a ``*_extra.c`` file (which jm
    never modifies).  Declare them in TOML as:

    .. code-block:: toml

        [module.resample]
        extra_types = ["HalfbandDecimatorDp", "HalfbandDecimatorR2C"]
    """
    v = cfg.get("module", {}).get(module, {}).get("extra_types", [])
    return list(v) if isinstance(v, (list, tuple)) else []


def extra_link_libs(cfg: dict, module: str) -> list[str]:
    """Return hand-declared extra link targets for a module's CMakeLists.

    These are appended to the generated ``target_link_libraries`` block and
    survive every ``jm apply`` / ``jm object`` call.  Declare them in TOML as:

    .. code-block:: toml

        [module.resample]
        extra_link_libs = ["resamp_core", "hbdecim_core", "m"]
    """
    v = cfg.get("module", {}).get(module, {}).get("extra_link_libs", [])
    return list(v) if isinstance(v, (list, tuple)) else []


def is_no_generate_module(cfg: dict, module: str) -> bool:
    """Return True if the module's files are entirely hand-written.

    A no_generate module gets only an add_subdirectory CMake entry from
    jm apply; no _ext.c, __init__.py, or test scaffolding is touched."""
    v = cfg.get("module", {}).get(module, {}).get("no_generate")
    return v is True or v == "true"


def c_deps(cfg: dict) -> list[str]:
    """Return C-only dependency subdirectory names declared under [project].

    These are pure-C libraries (no Python extension) whose add_subdirectory
    entries are maintained by jm apply inside the Components sentinel."""
    return list(cfg.get("project", {}).get("c_deps", []))


def depends_on(cfg: dict, component: str) -> list[str]:
    """Return the transitive C OBJECT library deps for a component.

    Each name in the list gets a target_sources line emitted *before* the
    component's own target_sources in the root CMakeLists, so the combined
    library target sees all required object files."""
    return list(cfg.get(component, {}).get("depends_on", []))


def array_args(cfg: dict, component: str) -> list[tuple[str, str]]:
    """Return declared array constructor args for component as [(name, dtype), ...]."""
    return [
        (a["name"], a.get("type") or a.get("dtype", ""))
        for a in cfg.get(component, {}).get("array_args", [])
    ]


def init_params(cfg: dict, component: str) -> list[tuple]:
    """Return --init-param entries as 8-tuples.

    ``(name, type, default, default_raw, real_type, real_create_fn, optional, create_fn)``

    ``default_raw`` overrides the type's parse_zero for the raw C variable.
    ``real_type`` / ``real_create_fn`` enable dtype-dispatch: when the array
    arrives as the ``real_type`` numpy dtype, ``real_create_fn`` is called
    instead of the default ``<component>_create``.
    ``optional`` / ``create_fn`` enable optional-array dispatch: when the
    caller supplies the array kwarg, ``create_fn`` is called instead of
    ``<component>_create``; when omitted, ``<component>_create`` is called
    with only the scalar params.  All fields default to ``""`` / ``False``
    when absent.  Callers may unpack defensively with ``param[:3]``.
    """
    return [
        (
            p["name"],
            p["type"],
            p.get("default", ""),
            p.get("default_raw", ""),
            p.get("real_type", ""),
            p.get("real_create_fn", ""),
            p.get("optional", False),
            p.get("create_fn", ""),
        )
        for p in cfg.get(component, {}).get("init_params", [])
    ]


def init_post_parse(cfg: dict, component: str) -> str:
    """Return the inline C snippet injected after PyArg_ParseTupleAndKeywords.

    Used to express dynamic defaults (e.g. ``noise_hi`` defaults to
    ``ref_len - 1`` when the caller omits it).  Empty string when absent.
    """
    return cfg.get(component, {}).get("init_post_parse", "")


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
        if not s.get("opaque")
    ]


def opaque_fields(cfg: dict, component: str) -> list[tuple[str, str]]:
    """State entries flagged ``opaque = true`` — pointer or handle fields
    emitted into the struct verbatim with no auto-getter/setter, no kwlist
    entry, and no create/reset assignments.  Lifecycle is the user's
    responsibility via ``create_impl`` / ``destroy_impl``."""
    return [
        (s["name"], s["type"])
        for s in cfg.get(component, {}).get("state", [])
        if s.get("opaque")
    ]


def schema_version(cfg: dict) -> int:
    """Return the project's schema version (1 for pre-schema projects)."""
    return int(cfg.get("project", {}).get("schema", 1))


def set_schema_version(cfg: dict, version: int) -> dict:
    """Set the schema version in-place and return cfg."""
    cfg.setdefault("project", {})["schema"] = str(version)
    return cfg


def project_name(cfg: dict) -> str:
    return cfg.get("project", {}).get("name", "")


def project_version(cfg: dict) -> str:
    return cfg.get("project", {}).get("version", "0.1.0")


def build_system(cfg: dict) -> str:
    """Return 'cmake' (default) or 'make'."""
    return cfg.get("project", {}).get("build", "cmake")


def is_perf(cfg: dict) -> bool:
    return cfg.get("project", {}).get("perf") == "true"


def is_pytest(cfg: dict) -> bool:
    return cfg.get("project", {}).get("pytest") == "true"


def is_pytest_benchmark(cfg: dict) -> bool:
    return cfg.get("project", {}).get("pytest_benchmark") == "true"


def from_new(
    name: str,
    version: str = "0.1.0",
    build_system: str = "cmake",
    perf: bool = False,
    pytest_: bool = False,
    pytest_benchmark_: bool = False,
) -> dict:
    return {
        "project": {
            "name": name,
            "version": version,
            "build": build_system,
            "perf": "true" if perf else "false",
            "pytest": "true" if pytest_ else "false",
            "pytest_benchmark": "true" if pytest_benchmark_ else "false",
            "schema": str(CURRENT_SCHEMA),
        }
    }


def arg_type(cfg: dict, component: str) -> str:
    return cfg.get(component, {}).get("arg_type", "float _Complex")


def return_type(cfg: dict, component: str) -> str:
    return cfg.get(component, {}).get("return_type", "float _Complex")


def class_name(cfg: dict, component: str) -> str | None:
    """Return the overridden Python class name, or None to use title-cased component."""
    return cfg.get(component, {}).get("class_name") or None


def add_component(
    cfg: dict,
    component: str,
    vars_: list[tuple[str, str, str]],
    arg_type_: str = "float _Complex",
    return_type_: str | None = None,
    array_args_: list[tuple[str, str]] = (),
    no_state_: bool = False,
    no_step_: bool = False,
    mutable_: bool = False,
    init_params_: list[tuple[str, str, str]] = (),
    class_name_: str | None = None,
    depends_on_: list[str] = (),
) -> dict:
    rt = (
        return_type_
        if return_type_ is not None
        else "void"
        if arg_type_.endswith("[]")
        else arg_type_
    )
    entry: dict = {
        "arg_type": arg_type_,
        "return_type": rt,
        "mutable": "true" if mutable_ else "false",
        "no_state": "true" if no_state_ else "false",
        "no_step": "true" if no_step_ else "false",
        "state": [{"name": n, "type": t, "default": d} for n, t, d in vars_],
    }
    if array_args_:
        entry["array_args"] = [{"name": n, "type": dt} for n, dt in array_args_]
    if init_params_:
        entry["init_params"] = []
        for p in init_params_:
            n, t, d = p[:3]
            rec = {"name": n, "type": t}
            if d:
                rec["default"] = d
            if len(p) > 3 and p[3]:
                rec["default_raw"] = p[3]
            if len(p) > 4 and p[4]:
                rec["real_type"] = p[4]
            if len(p) > 5 and p[5]:
                rec["real_create_fn"] = p[5]
            if len(p) > 6 and p[6]:
                rec["optional"] = True
            if len(p) > 7 and p[7]:
                rec["create_fn"] = p[7]
            entry["init_params"].append(rec)
    if class_name_:
        entry["class_name"] = class_name_
    if depends_on_:
        entry["depends_on"] = list(depends_on_)
    cfg[component] = entry
    return cfg


def _dump(cfg: dict) -> str:
    lines: list[str] = []

    proj = cfg.get("project", {})
    if proj:
        lines.append("[project]")
        for k, v in proj.items():
            if k == "c_deps":
                deps_str = ", ".join(f'"{d}"' for d in v)
                lines.append(f"c_deps = [{deps_str}]")
            else:
                lines.append(f'{k} = "{v}"')
        lines.append("")

    for mod, data in cfg.get("module", {}).items():
        lines.append(f"[module.{mod}]")
        if data.get("no_generate") in (True, "true"):
            lines.append('no_generate = "true"')
        else:
            objs = data.get("objects", [])
            objs_str = ", ".join(f'"{o}"' for o in objs)
            lines.append(f"objects = [{objs_str}]")
        extra_t = data.get("extra_types", [])
        if extra_t:
            types_str = ", ".join(f'"{t}"' for t in extra_t)
            lines.append(f"extra_types = [{types_str}]")
        extra = data.get("extra_link_libs", [])
        if extra:
            libs_str = ", ".join(f'"{lib}"' for lib in extra)
            lines.append(f"extra_link_libs = [{libs_str}]")
        lines.append("")
        for fn in data.get("functions", []):
            lines.append(f"[[module.{mod}.functions]]")
            lines.append(f'name = "{fn["name"]}"')
            if fn.get("doc"):
                lines.append(f'doc = "{fn["doc"]}"')
            if fn.get("return_type"):
                lines.append(f'return_type = "{fn["return_type"]}"')
            if fn.get("out_type"):
                lines.append(f'out_type = "{fn["out_type"]}"')
            if fn.get("max_results_param"):
                lines.append(f'max_results_param = "{fn["max_results_param"]}"')
            if fn.get("result_fields"):
                rf_parts = ", ".join(
                    f'{{name = "{f["name"]}", type = "{f["type"]}"}}'
                    for f in fn["result_fields"]
                )
                lines.append(f"result_fields = [{rf_parts}]")
            if fn.get("params"):
                parts = ", ".join(
                    f'{{name = "{p["name"]}", type = "{p["type"]}"}}'
                    for p in fn["params"]
                )
                lines.append(f"params = [{parts}]")
            if fn.get("inline"):
                lines.append("inline = true")
            lines.append("")

    for comp in components(cfg):
        comp_data = cfg[comp]
        scalar_keys = (
            "module",
            "arg_type",
            "return_type",
            "mutable",
            "no_state",
            "no_step",
            "class_name",
        )
        lines.append(f"[{comp}]")
        for k in scalar_keys:
            if k in comp_data:
                lines.append(f'{k} = "{comp_data[k]}"')
        if comp_data.get("depends_on"):
            deps_str = ", ".join(f'"{d}"' for d in comp_data["depends_on"])
            lines.append(f"depends_on = [{deps_str}]")
        lines.append("")
        for a in comp_data.get("array_args", []):
            lines.append(f"[[{comp}.array_args]]")
            lines.append(f'name = "{a["name"]}"')
            lines.append(f'type = "{a.get("type") or a.get("dtype", "")}"')
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
            if "default" in p:
                lines.append(f'default = "{p["default"]}"')
            if "default_raw" in p:
                lines.append(f'default_raw = "{p["default_raw"]}"')
            if "real_type" in p:
                lines.append(f'real_type = "{p["real_type"]}"')
            if "real_create_fn" in p:
                lines.append(f'real_create_fn = "{p["real_create_fn"]}"')
            if p.get("optional"):
                lines.append("optional = true")
            if "create_fn" in p:
                lines.append(f'create_fn = "{p["create_fn"]}"')
            lines.append("")
        if comp_data.get("init_post_parse"):
            ipp = comp_data["init_post_parse"].replace('"""', '\\"\\"\\"')
            lines.append(f'init_post_parse = """\n{ipp}\n"""')
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
            if m.get("none_on_empty"):
                lines.append("none_on_empty = true")
            if m.get("batch"):
                lines.append("batch = true")
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
                lines.append(f"out_divisor = {m['out_divisor']}")
            if m.get("bench") is False:
                lines.append("bench = false")
            if m.get("max_results"):
                lines.append(f"max_results = {m['max_results']}")
            if m.get("result_fields"):
                rf_parts = ", ".join(
                    f'{{name = "{f["name"]}", type = "{f["type"]}"}}'
                    for f in m["result_fields"]
                )
                lines.append(f"result_fields = [{rf_parts}]")
            if m.get("py_return_type"):
                lines.append(f'py_return_type = "{m["py_return_type"]}"')
            lines.append("")
        for p in comp_data.get("properties", []):
            lines.append(f"[[{comp}.properties]]")
            lines.append(f'name = "{p["name"]}"')
            lines.append(f'type = "{p.get("type") or p.get("ctype", "size_t")}"')
            if p.get("writable"):
                lines.append("writable = true")
            if p.get("field"):
                lines.append("field = true")
            if p.get("buf_field"):
                lines.append(f'buf_field = "{p["buf_field"]}"')
                lines.append(f'len_field = "{p.get("len_field", "n")}"')
            if p.get("valid_field"):
                lines.append(f'valid_field = "{p["valid_field"]}"')
            if p.get("expr"):
                lines.append(f'expr = "{p["expr"]}"')
            lines.append("")

    return "\n".join(lines)

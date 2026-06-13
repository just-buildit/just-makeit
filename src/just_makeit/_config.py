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

from __future__ import annotations

import re as _re

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib
from pathlib import Path
from typing import NamedTuple

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
    """Merge an included fragment into *cfg*.

    Top-level object sections are added. Under `[module]`, a fragment may
    declare a full `[module.X]` (the `modules/X.toml` layout) *or* only
    extend an existing module with `[[module.X.functions]]` (the
    `objects/*.toml` layout, where a per-object fragment contributes a
    module function). Function lists concatenate across fragments; every
    other module key is set once — a conflicting redefinition is an error,
    so a module declared in two places is caught rather than silently
    last-wins.
    """
    for key, value in fragment.items():
        if key == "project":
            raise ValueError(
                f"{source}: [project] must live in the manifest, "
                f"not in an included fragment."
            )
        if key == "module":
            for mod, mod_data in (value or {}).items():
                dest = cfg.setdefault("module", {}).setdefault(mod, {})
                if not isinstance(mod_data, dict):
                    continue
                for mk, mv in mod_data.items():
                    if mk == "functions":
                        dest.setdefault("functions", []).extend(mv)
                    elif mk in dest and dest[mk] != mv:
                        raise ValueError(
                            f"{source}: [module.{mod}].{mk} conflicts with an "
                            f"earlier definition — a module's config belongs "
                            f"in exactly one place (modules/{mod}.toml)."
                        )
                    else:
                        dest[mk] = mv
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


def _provenance(
    root: Path,
) -> tuple[dict[str, Path], dict[str, Path], list[str]]:
    """Re-derive which file each section currently lives in.

    Returns (owners, module_owners, include_list):

    - owners[key]        — file that owns top-level object section *key*.
    - module_owners[mod] — file that owns the `[module.mod]` declaration.
      A fragment owns a module only when it declares real config for it
      (any key beyond ``functions``); a fragment that merely contributes
      ``[[module.mod.functions]]`` does not claim ownership. Modules
      declared in the manifest are owned by the manifest.
    - include_list       — the manifest's `include` list (empty for a
      single-file project)."""
    owners: dict[str, Path] = {}
    module_owners: dict[str, Path] = {}
    manifest_path = root / FILENAME
    if not manifest_path.exists():
        return owners, module_owners, []
    with manifest_path.open("rb") as f:
        manifest = tomllib.load(f)
    for k in manifest:
        if k not in ("project", "module", "include"):
            owners[k] = manifest_path
    for mod in manifest.get("module", {}):
        module_owners[mod] = manifest_path
    include_list = list(manifest.get("include", []))
    for fragment_path in _resolve_includes(root, include_list):
        with fragment_path.open("rb") as f:
            fragment = tomllib.load(f)
        for k in fragment:
            if k not in ("project", "module", "include"):
                owners[k] = fragment_path
        for mod, data in fragment.get("module", {}).items():
            if isinstance(data, dict) and (set(data) - {"functions"}):
                module_owners[mod] = fragment_path
    return owners, module_owners, include_list


def _write_doc(path: Path, cfg: dict, include_list: list[str] | None) -> None:
    """Write cfg to path.

    For existing files the ``[project]`` section, ``[module.X]`` sections,
    and the ``include`` key are updated in-place using tomlkit so that user
    comments survive a round-trip.  Component sections (which contain
    ``[[comp.state]]`` repeated tables) are always rebuilt from ``_dump()``
    and appended after the preserved header — comment preservation inside
    repeated-table arrays is impractical with tomlkit.

    Falls back silently to plain ``_dump()`` if tomlkit is not installed
    (just-buildit does not propagate ``[project].dependencies`` to the wheel,
    so tomlkit may be absent in tool-installed environments; comment
    preservation is a nice-to-have, not a hard requirement).

    For brand-new files the output is identical to the previous plain-
    ``_dump()`` behaviour."""
    comps = {k: v for k, v in cfg.items() if k not in ("project", "module")}
    comp_text = _dump(comps)

    if not path.exists():
        text = _dump(cfg)
        if include_list:
            text = f"include = {_toml_string_array(include_list)}\n\n" + text
        path.write_text(text, encoding="utf-8")
        return

    try:
        import tomlkit as _tk
    except ModuleNotFoundError:
        # tomlkit not available — fall back to full rewrite (no comment preservation)
        text = _dump(cfg)
        if include_list:
            text = f"include = {_toml_string_array(include_list)}\n\n" + text
        path.write_text(text, encoding="utf-8")
        return

    def _sync(tbl: "_tk.items.Table", new_data: dict) -> None:
        for k, v in new_data.items():
            tbl[k] = v
        for k in list(tbl.keys()):
            if k not in new_data:
                del tbl[k]

    doc = _tk.loads(path.read_text(encoding="utf-8"))

    # -- include list ---------------------------------------------------------
    if include_list is not None:
        arr = _tk.array()
        for item in include_list:
            arr.append(item)
        doc["include"] = arr
    elif "include" in doc:
        del doc["include"]

    # -- [project] ------------------------------------------------------------
    new_proj = cfg.get("project")
    if new_proj:
        if "project" not in doc:
            doc.add("project", _tk.table())
        _sync(doc["project"], new_proj)
    elif "project" in doc:
        del doc["project"]

    # -- [module.X] -----------------------------------------------------------
    new_mod = cfg.get("module", {})
    if "module" not in doc:
        if new_mod:
            doc.add("module", _tk.table())
    if "module" in doc:
        mod_tbl = doc["module"]
        for mod, data in new_mod.items():
            if mod not in mod_tbl:
                mod_tbl.add(mod, _tk.table())
            _sync(mod_tbl[mod], data)
        for mod in list(mod_tbl.keys()):
            if mod not in new_mod:
                del mod_tbl[mod]
        if not new_mod:
            del doc["module"]

    # -- component sections ---------------------------------------------------
    # Strip old component keys; they will be replaced by _dump()-generated text.
    for k in list(doc.keys()):
        if k not in ("project", "module", "include"):
            del doc[k]

    header = _tk.dumps(doc).rstrip("\n")
    body = comp_text.strip()
    path.write_text(
        ((header + "\n\n" + body) if body else header).strip() + "\n",
        encoding="utf-8",
    )


def save(root: Path, cfg: dict) -> None:
    """Write cfg back to disk, routing each top-level object section to
    the file that owns it on disk. `[project]` / `[module.X]` always
    live in the manifest. New objects go to `objects/<name>.toml` when
    the project uses the split layout, or to the manifest otherwise.
    A fragment file that ends up with no sections is deleted."""
    manifest_path = root / FILENAME
    owners, module_owners, include_list = _provenance(root)
    split_layout = bool(include_list)

    # Group every section in cfg by destination file. Objects route to
    # their owning fragment (or objects/<name>.toml when new in a split
    # project); modules route to their owning fragment (or
    # modules/<name>.toml when new in a split project) instead of always
    # the manifest, so the fragment layout survives a mutating command.
    by_file: dict[Path, dict] = {}
    for key, value in cfg.items():
        if key in ("project", "module", "include", "app"):
            continue  # `app`, like `project`, always lives in the manifest
        if key in owners:
            dst = owners[key]
        elif split_layout:
            dst = root / "objects" / f"{key}.toml"
        else:
            dst = manifest_path
        by_file.setdefault(dst, {})[key] = value

    for mod, data in cfg.get("module", {}).items():
        if mod in module_owners:
            dst = module_owners[mod]
        elif split_layout:
            # Sanitize a dotted module id to its cname for the fragment file
            # name (modules/dsp_filters.toml) — one clean extension that still
            # matches the modules/*.toml include glob; the dotted key lives
            # inside the file.
            dst = root / "modules" / f"{module_paths(mod).cname}.toml"
        else:
            dst = manifest_path
        by_file.setdefault(dst, {}).setdefault("module", {})[mod] = data

    # Manifest always carries [project] / include + whatever object and
    # module sections route to it.
    manifest_content: dict = {}
    if "project" in cfg:
        manifest_content["project"] = cfg["project"]
    if cfg.get("app"):
        manifest_content["app"] = cfg["app"]  # gh-190: keep [app] in manifest
    manifest_content.update(by_file.get(manifest_path, {}))

    _write_doc(manifest_path, manifest_content, include_list or None)

    # Each fragment file gets only its remaining sections; an empty
    # fragment is removed.
    seen_fragments = {
        fp
        for fp in list(owners.values()) + list(module_owners.values())
        if fp != manifest_path
    }
    for fragment_path in seen_fragments:
        sections = by_file.get(fragment_path, {})
        if sections:
            fragment_path.parent.mkdir(parents=True, exist_ok=True)
            _write_doc(fragment_path, sections, None)
        else:
            fragment_path.unlink(missing_ok=True)

    # Brand-new fragment files (new object/module in a split project).
    for fragment_path, sections in by_file.items():
        if fragment_path == manifest_path:
            continue
        if fragment_path in seen_fragments:
            continue
        fragment_path.parent.mkdir(parents=True, exist_ok=True)
        _write_doc(fragment_path, sections, None)


def components(cfg: dict) -> list[str]:
    """Return component names — all top-level keys except reserved sections."""
    return [k for k in cfg if k not in ("project", "module", "app")]


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


class ModulePaths(NamedTuple):
    """Derived forms of a (possibly dotted) module id (gh nested modules).

    A module name like ``dsp.filters`` is a *package path*: it nests the
    extension under ``src/<pkg>/dsp/filters/`` and imports as
    ``pkg.dsp.filters``. The single name plays three roles that nesting splits
    apart, so each is derived once here:

    ``id``      The canonical dotted name — the TOML key under ``[module.X]``.
    ``leaf``    Final segment (``filters``) — the ``.so`` basename, ``PyInit_``
                symbol, ``.m_name``, and the ``from .<leaf> import`` line.
    ``cname``   Dots→underscores (``dsp_filters``) — the CMake target, the
                ``native/src/<cname>/`` directory and C file prefixes. A single
                ``\\w+`` token, so the flat native tree and apply/remove
                ``add_subdirectory`` machinery are untouched.
    ``pypath``  Dots→slashes (``dsp/filters``) — the Python output directory
                under ``src/<pkg>/`` and the CMake ``LIBRARY_OUTPUT_DIRECTORY``.
    ``parents`` Intermediate package names (``["dsp"]``) that need a plain
                ``__init__.py`` marker for ``pkg.dsp`` to be importable.

    Invariant: for a *dotless* id, ``leaf == cname == pypath == id`` and
    ``parents == ()`` — every field equals today's string, so flat modules
    render byte-for-byte unchanged.

    >>> ModulePaths.of("dsp.filters")
    ModulePaths(id='dsp.filters', leaf='filters', cname='dsp_filters', pypath='dsp/filters', parents=('dsp',))
    >>> ModulePaths.of("dsp")
    ModulePaths(id='dsp', leaf='dsp', cname='dsp', pypath='dsp', parents=())
    """

    id: str
    leaf: str
    cname: str
    pypath: str
    parents: tuple[str, ...]

    @classmethod
    def of(cls, module_id: str) -> "ModulePaths":
        segs = module_id.split(".")
        return cls(
            id=module_id,
            leaf=segs[-1],
            cname="_".join(segs),
            pypath="/".join(segs),
            parents=tuple(segs[:-1]),
        )


def module_paths(module_id: str) -> ModulePaths:
    """Derived path/identifier forms for a (possibly dotted) module id."""
    return ModulePaths.of(module_id)


def module_cnames(cfg: dict) -> set[str]:
    """CMake-target / native-dir names (cname) of every module.

    The top ``CMakeLists.txt`` carries ``add_subdirectory(native/src/<cname>)``
    lines; apply classifies those blocks by membership in this set, so it must
    compare against the cname token the regex captures, not the dotted id.
    """
    return {module_paths(m).cname for m in modules(cfg)}


def validate_module_id(module_id: str) -> str | None:
    """Return an error message for an invalid module id, else ``None``.

    Each dot-separated segment must be a valid identifier (letters/digits/
    underscores, not starting with a digit). Empty, leading/trailing-dot, and
    double-dot names are rejected.
    """
    if not module_id:
        return "module name must not be empty"
    segs = module_id.split(".")
    for seg in segs:
        if not seg or not seg.replace("_", "").isalnum() or seg[0].isdigit():
            return (
                f"'{module_id}' is not a valid module name.\n"
                "Use lowercase letters, digits, and underscores only; dotted "
                "names (e.g. dsp.filters) nest the module in a subpackage. "
                "Each dot-separated segment must not start with a digit."
            )
    return None


def _module_key(mod: str) -> str:
    """TOML table key for a module id — quoted when dotted.

    ``[module."dsp.filters"]`` keeps the dotted id a single key; the bare
    ``[module.dsp.filters]`` would parse as nested tables.
    """
    return f'"{mod}"' if "." in mod else mod


def _truthy(v: object) -> bool:
    """Accept both ``"true"`` (canonical string form written by jm) and
    Python ``True`` (what tomllib returns for ``key = true``).  Anything
    else — ``"false"``, ``False``, ``None``, missing — is false."""
    return v is True or v == "true"


def is_mutable(cfg: dict, component: str) -> bool:
    """Return True if the component was scaffolded with --mutable."""
    return _truthy(cfg.get(component, {}).get("mutable"))


def is_no_state(cfg: dict, component: str) -> bool:
    """Return True if the component was scaffolded with --no-state."""
    return _truthy(cfg.get(component, {}).get("no_state"))


def is_streamable(cfg: dict, component: str) -> bool:
    """Return True if the component was scaffolded with --streamable.

    A streamable object gets a generated ``stream()`` generator method and
    ``__iter__`` that drive its producer (the ``variable_output`` method if
    one exists, else the built-in ``steps``) block by block.
    """
    return _truthy(cfg.get(component, {}).get("streamable"))


def is_async_stream(cfg: dict, component: str) -> bool:
    """Return True if the component was scaffolded with --async-stream.

    Adds `__aiter__` / `__anext__` to the generated stream iterator (and an
    async `__aiter__` to the object) on top of the synchronous `stream()` /
    `__iter__`.  Implies `streamable`.
    """
    return _truthy(cfg.get(component, {}).get("async_stream"))


def stream_block_default(cfg: dict, component: str) -> int:
    """Default block size for the generated ``stream()`` / ``__iter__``.

    This is the producer argument used when the caller passes no explicit
    block (e.g. ``for blk in obj:``).  Falls back to 1024 when unset.
    """
    raw = cfg.get(component, {}).get("stream_block_default")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 1024


def is_no_step(cfg: dict, component: str) -> bool:
    """Return True if the component was scaffolded with --no-step."""
    return _truthy(cfg.get(component, {}).get("no_step"))


def step_delegates(cfg: dict, component: str) -> bool:
    """Return True if step() should be generated as a delegator to steps().

    gh-208: with ``step_delegates_to_steps = true`` the per-sample algorithm
    lives only in steps() and step() forwards to ``steps(.., 1)``, so the two
    stay byte-identical under ``-ffast-math``.
    """
    return _truthy(cfg.get(component, {}).get("step_delegates_to_steps"))


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


def extra_include_dirs(cfg: dict, module: str) -> list[str]:
    """Return hand-declared extra include directories for a module's CMakeLists.

    Counterpart to :func:`extra_link_libs` for ``target_include_directories``.
    CMake variables (``${...}``) are honoured.  Declare in TOML as:

    .. code-block:: toml

        [module.source]
        extra_include_dirs = ["${DOPPLER_INCLUDE_DIR}"]
    """
    v = cfg.get("module", {}).get(module, {}).get("extra_include_dirs", [])
    return list(v) if isinstance(v, (list, tuple)) else []


def module_reexports(cfg: dict, module: str) -> dict[str, list[str]]:
    """Return symbols a module's ``__init__.py`` re-exports from siblings.

    A module subpackage's generated ``__init__.py`` already re-exports its own
    C-extension types and functions. ``reexports`` additionally pulls names
    from a *sibling* extension in the same package — typically a
    ``no_generate`` module whose ``.pyi`` and binding are hand-written (e.g. a
    PyCapsule functional API) — folding them into both the import block and
    ``__all__`` so the glue stays hands-off and regenerates cleanly. Declare in
    TOML as an inline table mapping submodule -> exported names:

    .. code-block:: toml

        [module.ddc]
        objects = ["ddc", "ddcr"]
        reexports = { ddc_fn = [
            "ddcr_create", "ddcr_execute", "ddcr_destroy",
        ] }

    Returns ``{submodule: [name, ...]}`` in declared order (empty if unset).
    """
    raw = cfg.get("module", {}).get(module, {}).get("reexports", {})
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for sub, names in raw.items():
        if isinstance(names, (list, tuple)):
            out[str(sub)] = [str(n) for n in names]
    return out


def is_no_generate_module(cfg: dict, module: str) -> bool:
    """Return True if the module's files are entirely hand-written.

    A no_generate module gets only an add_subdirectory CMake entry from
    jm apply; no _ext.c, __init__.py, or test scaffolding is touched."""
    return _truthy(cfg.get("module", {}).get(module, {}).get("no_generate"))


def c_deps(cfg: dict) -> list[str]:
    """Return C-only dependency subdirectory names declared under [project].

    These are pure-C libraries (no Python extension) whose add_subdirectory
    entries are maintained by jm apply inside the Components sentinel."""
    return list(cfg.get("project", {}).get("c_deps", []))


def status_allow(cfg: dict) -> list[str]:
    """Return path patterns under [project] that `jm status` treats as
    known-accepted deviations (reported but not counted as drift).

    Each entry is an exact POSIX relative path or an fnmatch glob, e.g.
    ``status_allow = ["native/inc/ddc/ddc_core.h", "native/inc/*/legacy_*.h"]``.
    Pairs with the ``--allow`` CLI flag (gh-140)."""
    return list(cfg.get("project", {}).get("status_allow", []))


def find_packages(cfg: dict) -> list[str]:
    """Return CMake package names declared under [project].

    Each name is emitted as ``find_package(X REQUIRED)`` inside the
    ``# ── External deps`` sentinel block in the top CMakeLists.txt,
    maintained by ``jm apply``.  Declare in TOML as:

    .. code-block:: toml

        [project]
        find_packages = ["Doppler"]
    """
    v = cfg.get("project", {}).get("find_packages", [])
    return list(v) if isinstance(v, (list, tuple)) else []


def pkg_modules(cfg: dict) -> list[str]:
    """Return pkg-config module names declared under [project].

    Each name X is emitted as
    ``pkg_check_modules(X_upper REQUIRED IMPORTED_TARGET X)`` inside the
    ``# ── External deps`` block, maintained by ``jm apply``.  The
    resulting imported target is ``PkgConfig::X_UPPER``.  Declare in TOML as:

    .. code-block:: toml

        [project]
        pkg_modules = ["doppler"]
    """
    v = cfg.get("project", {}).get("pkg_modules", [])
    return list(v) if isinstance(v, (list, tuple)) else []


def component_extra_link_libs(cfg: dict, component: str) -> list[str]:
    """Return hand-declared extra link targets for a standalone component.

    Appended to the generated ``target_link_libraries`` blocks for both the
    Python extension and the CTest executable.  Survive every ``jm apply``.
    Declare in TOML as:

    .. code-block:: toml

        [tone]
        extra_link_libs = ["PkgConfig::DOPPLER"]
    """
    v = cfg.get(component, {}).get("extra_link_libs", [])
    return list(v) if isinstance(v, (list, tuple)) else []


def component_extra_include_dirs(cfg: dict, component: str) -> list[str]:
    """Return hand-declared extra include directories for a standalone component.

    Counterpart to :func:`component_extra_link_libs`.  Declare in TOML as:

    .. code-block:: toml

        [tone]
        extra_include_dirs = ["${DOPPLER_INCLUDE_DIR}"]
    """
    v = cfg.get(component, {}).get("extra_include_dirs", [])
    return list(v) if isinstance(v, (list, tuple)) else []


def _dep_name(d) -> str:
    """Name of a depends_on entry — a bare string or a ``{name, link}`` table."""
    return d["name"] if isinstance(d, dict) else d


def _dep_links(d) -> bool:
    """Whether a depends_on entry also owns the consuming target's link line."""
    return bool(d.get("link")) if isinstance(d, dict) else False


def depends_on(cfg: dict, component: str) -> list[str]:
    """Return the transitive C OBJECT library deps for a component.

    Each name in the list gets a target_sources line emitted *before* the
    component's own target_sources in the root CMakeLists, so the combined
    library target sees all required object files. An entry may be a bare
    string (``"fir"``) or a ``{name = "fir", link = true}`` table (gh-225);
    this returns the names either way (header includes + aggregate-lib
    objects apply to both forms)."""
    return [_dep_name(d) for d in cfg.get(component, {}).get("depends_on", [])]


def depends_on_raw(cfg: dict, component: str) -> list:
    """Raw depends_on entries — bare strings and/or ``{name, link}`` tables.

    Callers that must preserve the ``link`` flag (the replay/scaffold path that
    re-persists the manifest) take this; header/object-only callers take
    :func:`depends_on`, which flattens to names."""
    return list(cfg.get(component, {}).get("depends_on", []))


def dep_names(entries) -> list[str]:
    """Names from a raw depends_on list (each a string or ``{name, link}``)."""
    return [_dep_name(d) for d in entries]


def dep_link_libs(entries) -> list[str]:
    """``<name>_core`` link targets for raw entries marked ``link = true``.

    A ``{name = "fir", link = true}`` entry means jm owns the link: the
    dependency's ``<name>_core`` is added directly to the consuming target's
    ``target_link_libraries``, so its symbols resolve in the built ``.so``
    without a manual ``extra_link_libs`` + CMakeLists edit (gh-225). CMake does
    not pull a depends_on OBJECT lib's objects transitively into the final
    ``.so`` (see gh-160), so the link must be direct on the consuming target.
    Names are normalised to the ``<name>_core`` OBJECT-lib target."""
    out: list[str] = []
    for d in entries:
        if _dep_links(d):
            name = _dep_name(d)
            out.append(name if name.endswith("_core") else f"{name}_core")
    return out


def depends_link_libs(cfg: dict, component: str) -> list[str]:
    """``<name>_core`` link targets for a component's ``link = true`` deps.

    The cfg-based convenience wrapper over :func:`dep_link_libs`; used where the
    component's manifest section is already present (module aggregation, apply
    header injection). Render paths that run *before* the section is persisted
    (``_init.run``) call :func:`dep_link_libs` on the depends_on param instead."""
    return dep_link_libs(depends_on_raw(cfg, component))


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


def no_ctor_names(cfg: dict, component: str) -> frozenset[str]:
    """Names of non-opaque state entries flagged ``no_ctor = true``.

    These fields appear in the struct, have auto-getter/setter, and are
    included in reset_assignments — but are NOT exposed as constructor
    parameters.  The C create() signature omits them; they are
    silently initialised to their TOML default inside create_assignments
    (or inside create_impl if the user overrides it)."""
    return frozenset(
        s["name"]
        for s in cfg.get(component, {}).get("state", [])
        if s.get("no_ctor") and not s.get("opaque")
    )


def schema_version(cfg: dict) -> int:
    """Return the project's schema version (1 for pre-schema projects)."""
    return int(cfg.get("project", {}).get("schema", 1))


def set_schema_version(cfg: dict, version: int) -> dict:
    """Set the schema version in-place and return cfg."""
    cfg.setdefault("project", {})["schema"] = str(version)
    return cfg


def jm_cli_version() -> str:
    """The running just-makeit version (best-effort; 'unknown' if unknown)."""
    try:
        from importlib.metadata import version

        return version("just-makeit")
    except Exception:
        return "unknown"


def jm_version(cfg: dict) -> str:
    """The just-makeit version that last generated/applied this project.

    Empty string for projects created before the `jm_version` stamp (gh-183).
    """
    return cfg.get("project", {}).get("jm_version", "")


def set_jm_version(cfg: dict, ver: str) -> dict:
    """Record the generating just-makeit version in-place; return cfg."""
    cfg.setdefault("project", {})["jm_version"] = ver
    return cfg


def stamp_jm_version(root: Path, cfg: dict) -> str | None:
    """Record the running jm version in `[project].jm_version`, monotonically.

    Surgically rewrites only the `jm_version` line in the main manifest (no
    fragment re-dump / churn). Never downgrades the record — a stale CLI keeps
    warning (gh-183) instead of masking itself. Returns the version written, or
    None if nothing changed. Updates `cfg` in place too.
    """
    running = jm_cli_version()
    if running == "unknown":
        return None
    recorded = jm_version(cfg)
    if recorded == running:
        return None
    if recorded and version_tuple(running) < version_tuple(recorded):
        return None  # don't let an older CLI downgrade the record
    mp = root / FILENAME
    try:
        text = mp.read_text(encoding="utf-8")
    except OSError:
        return None
    if _re.search(r"^[ \t]*jm_version[ \t]*=", text, _re.M):
        text = _re.sub(
            r"^([ \t]*jm_version[ \t]*=[ \t]*).*$",
            lambda m: m.group(1) + f'"{running}"',
            text,
            count=1,
            flags=_re.M,
        )
    elif _re.search(r"^\[project\]", text, _re.M):
        text = _re.sub(
            r"^(\[project\][^\n]*\n)",
            lambda m: m.group(1) + f'jm_version = "{running}"\n',
            text,
            count=1,
            flags=_re.M,
        )
    else:
        return None
    mp.write_text(text, encoding="utf-8")
    cfg.setdefault("project", {})["jm_version"] = running
    return running


def version_tuple(s: str) -> tuple:
    """Parse 'X.Y.Z…' into a comparable int tuple (non-numeric tail dropped).

    Pre-release suffixes (``0.16.0rc1``) compare by their numeric prefix, so
    ``0.16.0rc1`` and ``0.16.0`` tuple-compare equal — good enough for a skew
    warning (we don't need to order pre-releases).
    """
    out = []
    for part in s.split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


def project_name(cfg: dict) -> str:
    return cfg.get("project", {}).get("name", "")


def project_version(cfg: dict) -> str:
    return cfg.get("project", {}).get("version", "0.1.0")


def build_system(cfg: dict) -> str:
    """Return 'cmake' (default) or 'make'."""
    return cfg.get("project", {}).get("build", "cmake")


_DEFAULT_PLATFORMS = ["linux", "macos"]


def project_platforms(cfg: dict) -> list[str]:
    """Target platforms declared under ``[project] platforms``.

    Defaults to ``["linux", "macos"]`` when unset — Windows is **opt-in**
    (gh-213). A project targets Windows only by listing ``"windows"`` here,
    which is what makes jm emit the MinGW runtime-DLL CMake boilerplate.
    """
    v = cfg.get("project", {}).get("platforms")
    if not isinstance(v, (list, tuple)) or not v:
        return list(_DEFAULT_PLATFORMS)
    return [str(p) for p in v]


def is_windows_target(cfg: dict) -> bool:
    """True if the project lists ``windows`` in ``[project] platforms``."""
    return "windows" in (p.lower() for p in project_platforms(cfg))


def is_perf(cfg: dict) -> bool:
    return _truthy(cfg.get("project", {}).get("perf"))


def is_pytest(cfg: dict) -> bool:
    return _truthy(cfg.get("project", {}).get("pytest"))


def is_pytest_benchmark(cfg: dict) -> bool:
    return _truthy(cfg.get("project", {}).get("pytest_benchmark"))


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
            "jm_version": jm_cli_version(),
        }
    }


def app_config(cfg: dict) -> dict:
    """Return the [app] section, or an empty dict if absent."""
    return cfg.get("app", {})


def set_app(
    cfg: dict,
    target: str,
    name: str,
    object_: str | None = None,
    function: str | None = None,
    module: str | None = None,
) -> dict:
    """Write the [app] target/name and its source (object, or function+module),
    preserving any [[app.flags]]."""
    app = cfg.get("app", {})
    app.update({"target": target, "name": name})
    if function is not None:
        app["function"] = function
        app["module"] = module or ""
        app.pop("object", None)
    else:
        app["object"] = object_
        app.pop("function", None)
        if module:
            app["module"] = module  # owning module (gh-187 console scoping)
        else:
            app.pop("module", None)
    cfg["app"] = app
    return cfg


def app_flags(cfg: dict) -> list[dict]:
    """Return declared [[app.flags]] (empty list if none)."""
    return list(cfg.get("app", {}).get("flags", []))


def add_app_flag(cfg: dict, flag: dict) -> dict:
    """Add/replace an [[app.flags]] entry, keyed by name."""
    app = cfg.setdefault("app", {})
    flags = app.setdefault("flags", [])
    flags[:] = [f for f in flags if f.get("name") != flag["name"]]
    flags.append(
        {
            k: flag[k]
            for k in ("name", "type", "default", "help")
            if flag.get(k) not in (None, "")
        }
    )
    return cfg


def app_commands(cfg: dict) -> list[dict]:
    """Return declared [[app.commands]] (empty list if none)."""
    return list(cfg.get("app", {}).get("commands", []))


def add_app_command(cfg: dict, command: dict) -> dict:
    """Add/replace an [[app.commands]] entry, keyed by name. A command is
    {name, help, flags: [{name, type, default, help}]}."""
    app = cfg.setdefault("app", {})
    cmds = app.setdefault("commands", [])
    cmds[:] = [c for c in cmds if c.get("name") != command["name"]]
    entry = {"name": command["name"]}
    if command.get("help"):
        entry["help"] = command["help"]
    if command.get("flags"):
        entry["flags"] = [dict(f) for f in command["flags"]]
    cmds.append(entry)
    return cfg


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
    step_delegates_: bool = False,
    streamable_: bool = False,
    async_stream_: bool = False,
    stream_block_default_: "int | None" = None,
    init_params_: list[tuple[str, str, str]] = (),
    class_name_: str | None = None,
    depends_on_: list[str] = (),
    opaque_fields_: "list[tuple[str, str]]" = (),
    no_ctor_names_: "frozenset[str]" = frozenset(),
    extra_link_libs_: list[str] = (),
    extra_include_dirs_: list[str] = (),
) -> dict:
    rt = (
        return_type_
        if return_type_ is not None
        else "void"
        if arg_type_.endswith("[]")
        else arg_type_
    )
    state_entries = [
        {
            "name": n,
            "type": t,
            "default": d,
            **({"no_ctor": True} if n in no_ctor_names_ else {}),
        }
        for n, t, d in vars_
    ]
    state_entries += [
        {"name": n, "type": t, "opaque": True} for n, t in opaque_fields_
    ]
    entry: dict = {
        "arg_type": arg_type_,
        "return_type": rt,
        "mutable": "true" if mutable_ else "false",
        "no_state": "true" if no_state_ else "false",
        "no_step": "true" if no_step_ else "false",
        "state": state_entries,
    }
    # Only persisted when set — keeps existing manifests byte-identical so
    # non-streamable objects produce no golden-output churn.
    if step_delegates_:
        entry["step_delegates_to_steps"] = "true"
    if streamable_:
        entry["streamable"] = "true"
    if async_stream_:
        entry["async_stream"] = "true"
    if stream_block_default_ is not None:
        entry["stream_block_default"] = str(stream_block_default_)
    if array_args_:
        entry["array_args"] = [
            {"name": n, "type": dt} for n, dt in array_args_
        ]
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
    if extra_link_libs_:
        entry["extra_link_libs"] = list(extra_link_libs_)
    if extra_include_dirs_:
        entry["extra_include_dirs"] = list(extra_include_dirs_)
    cfg[component] = entry
    return cfg


def _doc_assign(value: str) -> str:
    """Render ``doc = ...`` for the TOML dump.

    Multi-line docstrings use a TOML basic multi-line string; single-line
    docs use a basic string with quotes/backslashes escaped.
    """
    if "\n" in value:
        # strip("\n") for round-trip idempotency (gh-192) — see _dump impl keys.
        body = (
            value.replace("\\", "\\\\").replace('"""', '\\"\\"\\"').strip("\n")
        )
        return f'doc = """\n{body}\n"""'
    body = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'doc = "{body}"'


def _dump(cfg: dict) -> str:
    lines: list[str] = []

    proj = cfg.get("project", {})
    if proj:
        lines.append("[project]")
        for k, v in proj.items():
            if k in ("c_deps", "find_packages", "pkg_modules", "platforms"):
                items_str = ", ".join(f'"{x}"' for x in v)
                lines.append(f"{k} = [{items_str}]")
            else:
                lines.append(f'{k} = "{v}"')
        lines.append("")

    for mod, data in cfg.get("module", {}).items():
        lines.append(f"[module.{_module_key(mod)}]")
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
        extra_inc = data.get("extra_include_dirs", [])
        if extra_inc:
            inc_str = ", ".join(f'"{d}"' for d in extra_inc)
            lines.append(f"extra_include_dirs = [{inc_str}]")
        reexp = data.get("reexports", {})
        if isinstance(reexp, dict) and reexp:
            parts = []
            for sub, names in reexp.items():
                names_str = ", ".join(f'"{n}"' for n in names)
                parts.append(f"{sub} = [{names_str}]")
            lines.append(f"reexports = {{ {', '.join(parts)} }}")
        lines.append("")
        for fn in data.get("functions", []):
            lines.append(f"[[module.{_module_key(mod)}.functions]]")
            lines.append(f'name = "{fn["name"]}"')
            if fn.get("doc"):
                lines.append(f'doc = "{fn["doc"]}"')
            if fn.get("return_type"):
                lines.append(f'return_type = "{fn["return_type"]}"')
            if fn.get("out_type"):
                lines.append(f'out_type = "{fn["out_type"]}"')
            if fn.get("max_results_param"):
                lines.append(
                    f'max_results_param = "{fn["max_results_param"]}"'
                )
            if fn.get("result_fields"):
                rf_parts = ", ".join(
                    f'{{name = "{f["name"]}", type = "{f["type"]}"}}'
                    for f in fn["result_fields"]
                )
                lines.append(f"result_fields = [{rf_parts}]")
            if fn.get("params"):
                _emit = []
                for p in fn["params"]:
                    base = f'name = "{p["name"]}", type = "{p["type"]}"'
                    # `mutable` is a synonym for `out`; canonicalise on dump.
                    if p.get("out") or p.get("mutable"):
                        base += ", out = true"
                    _emit.append("{" + base + "}")
                lines.append(f"params = [{', '.join(_emit)}]")
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
            "step_delegates_to_steps",
            "streamable",
            "async_stream",
            "stream_block_default",
            "class_name",
        )
        lines.append(f"[{comp}]")
        for k in scalar_keys:
            if k in comp_data:
                lines.append(f'{k} = "{comp_data[k]}"')
        if comp_data.get("doc"):
            lines.append(_doc_assign(comp_data["doc"]))
        if comp_data.get("depends_on"):
            parts = []
            for d in comp_data["depends_on"]:
                if isinstance(d, dict):
                    inner = f'name = "{d["name"]}"'
                    if d.get("link"):
                        inner += ", link = true"
                    parts.append(f"{{ {inner} }}")
                else:
                    parts.append(f'"{d}"')
            lines.append(f"depends_on = [{', '.join(parts)}]")
        if comp_data.get("extra_link_libs"):
            libs_str = ", ".join(
                f'"{lib}"' for lib in comp_data["extra_link_libs"]
            )
            lines.append(f"extra_link_libs = [{libs_str}]")
        if comp_data.get("extra_include_dirs"):
            inc_str = ", ".join(
                f'"{d}"' for d in comp_data["extra_include_dirs"]
            )
            lines.append(f"extra_include_dirs = [{inc_str}]")
        # Custom C bodies, as heredocs. Emitted here — after the scalar keys,
        # BEFORE any [[comp.*]] sub-table — so a C.load/C.save round-trip
        # preserves them and TOML re-parses them onto the component (not the
        # last sub-table entry). Without this, re-saving a fragment silently
        # drops hand-written create/reset/destroy/step bodies.
        for _impl_key in ("impl", "create_impl", "reset_impl", "destroy_impl"):
            if comp_data.get(_impl_key):
                # strip("\n") so a load→dump round-trip is idempotent: TOML keeps
                # the trailing newline from `"""\n{body}\n"""`, which would
                # otherwise accumulate a blank line per re-dump (gh-192).
                _body = (
                    comp_data[_impl_key]
                    .replace('"""', '\\"\\"\\"')
                    .strip("\n")
                )
                lines.append(f'{_impl_key} = """\n{_body}\n"""')
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
            if "default" in s:
                lines.append(f'default = "{s["default"]}"')
            if s.get("opaque"):
                lines.append("opaque = true")
            if s.get("no_ctor"):
                lines.append("no_ctor = true")
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
            ipp = (
                comp_data["init_post_parse"]
                .replace('"""', '\\"\\"\\"')
                .strip("\n")
            )
            lines.append(f'init_post_parse = """\n{ipp}\n"""')
            lines.append("")
        for m in comp_data.get("methods", []):
            lines.append(f"[[{comp}.methods]]")
            lines.append(f'name = "{m["name"]}"')
            if m.get("doc"):
                lines.append(_doc_assign(m["doc"]))
            if m.get("arg_type"):
                lines.append(f'arg_type = "{m["arg_type"]}"')
            if m.get("return_type"):
                lines.append(f'return_type = "{m["return_type"]}"')
            if m.get("varargs"):
                lines.append("varargs = true")
            if m.get("variable_output"):
                lines.append("variable_output = true")
            if m.get("pass_capacity"):
                lines.append("pass_capacity = true")
            if m.get("nogil"):
                lines.append("nogil = true")
            if m.get("none_on_empty"):
                lines.append("none_on_empty = true")
            if m.get("batch"):
                lines.append("batch = true")
            if m.get("multi_output"):
                mo_str = ", ".join(f'"{t}"' for t in m["multi_output"])
                lines.append(f"multi_output = [{mo_str}]")
            _ea = m.get("extra_args") or m.get("params")
            if _ea:
                _ekey = "extra_args" if "extra_args" in m else "params"
                parts = ", ".join(
                    f'{{name = "{p["name"]}", type = "{p["type"]}"}}'
                    for p in _ea
                )
                lines.append(f"{_ekey} = [{parts}]")
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
            if m.get("max_out"):
                lines.append(f"max_out = {m['max_out']}")
            lines.append("")
        for p in comp_data.get("properties", []):
            lines.append(f"[[{comp}.properties]]")
            lines.append(f'name = "{p["name"]}"')
            if p.get("doc"):
                lines.append(_doc_assign(p["doc"]))
            lines.append(
                f'type = "{p.get("type") or p.get("ctype", "size_t")}"'
            )
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

    app = cfg.get("app", {})
    if app.get("target"):
        lines.append("[app]")
        lines.append(f'target = "{app["target"]}"')
        lines.append(f'name = "{app["name"]}"')
        if app.get("function") is not None:
            lines.append(f'function = "{app["function"]}"')
            lines.append(f'module = "{app.get("module", "")}"')
        elif app.get("object"):
            lines.append(f'object = "{app["object"]}"')
            if app.get("module"):  # owning module (gh-187 console scoping)
                lines.append(f'module = "{app["module"]}"')
        lines.append("")
        for f in app.get("flags", []):
            lines.append("[[app.flags]]")
            lines.append(f'name = "{f["name"]}"')
            lines.append(f'type = "{f["type"]}"')
            if f.get("default") not in (None, ""):
                lines.append(f'default = "{f["default"]}"')
            if f.get("help"):
                lines.append(f'help = "{f["help"]}"')
            lines.append("")
        for c in app.get("commands", []):
            lines.append("[[app.commands]]")
            lines.append(f'name = "{c["name"]}"')
            if c.get("help"):
                lines.append(f'help = "{c["help"]}"')
            if c.get("flags"):
                parts = ", ".join(
                    "{"
                    + ", ".join(
                        f'{k} = "{fl[k]}"'
                        for k in ("name", "type", "default", "help")
                        if fl.get(k) not in (None, "")
                    )
                    + "}"
                    for fl in c["flags"]
                )
                lines.append(f"flags = [{parts}]")
            lines.append("")

    return "\n".join(lines)

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
CURRENT_SCHEMA = 7


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


def _tk_value(value, _tk):
    """Convert a plain Python value into the tomlkit item it should become.

    The only interesting case is a list of dicts: assigning it directly would
    produce an inline array of inline tables (``x = [{a = 1}]``) rather than
    the repeated-table form (``[[x]]``) the manifest uses everywhere. Build an
    AoT explicitly so a newly added ``[[<comp>.warnings]]`` looks like every
    other table array in the file.
    """
    if (
        isinstance(value, list)
        and value
        and all(isinstance(v, dict) for v in value)
    ):
        aot = _tk.aot()
        for entry in value:
            tbl = _tk.table()
            for k, v in entry.items():
                tbl[k] = v
            aot.append(tbl)
        return aot
    return value


def _sync(tbl, new_data: dict, _tk) -> None:
    """Update `tbl` in place to match `new_data`, touching only what changed.

    Skipping unchanged keys is load-bearing, and specifically for *layout*
    rather than comments — tomlkit does carry a key's comments across a
    same-value reassignment, so that part would survive either way. What does
    not survive is the authored shape: a hand-formatted inline array like::

        # why these links are explicit
        depends_on = [
            { name = "corr2d", link = true },
        ]

    round-trips fine when left alone, but reassigning it sends the value
    through `_tk_value`, which builds a list of dicts as an AoT — rewriting it
    as ``[[acq.depends_on]]``. Same TOML semantics, different file. Comparing
    first means an untouched key keeps exactly the form its author chose.

    (A key whose value genuinely *changes* is still re-emitted in jm's
    canonical shape. That is the honest trade: jm owns the value, the author
    owns the layout of values jm did not touch.)

    tomlkit items compare equal to the plain values they wrap, so this is a
    plain ``==``.

    Two kinds of key are dropped rather than written, both of which ``_dump()``
    discarded for free by only emitting keys it recognised — a sync has to skip
    them deliberately or tomlkit raises ConvertError:

    - **Underscore-prefixed**: transient in-memory state, not manifest data.
      ``_object._regenerate_module`` stashes ``_doc_blocks`` (parsed DoxyBlock
      objects) on the cfg for the render chain.
    - **None-valued**: TOML has no null, so None means "absent". Filtering it
      here lets the trailing delete-loop remove the key, which is the right
      semantics — an unset value should leave no trace in the file.
    """
    new_data = {
        k: v
        for k, v in new_data.items()
        if not k.startswith("_") and v is not None
    }
    for k, v in new_data.items():
        # Unchanged → leave the parsed item, and its comments, exactly as
        # authored. tomlkit items compare equal to the plain values they wrap.
        if k in tbl and tbl[k] == v:
            continue
        tbl[k] = _tk_value(v, _tk)
    for k in list(tbl.keys()):
        if k not in new_data:
            del tbl[k]


def _write_doc(path: Path, cfg: dict, include_list: list[str] | None) -> None:
    """Write cfg to path, preserving what the user wrote (gh-491).

    For an existing file every section — ``[project]``, ``[module.X]``, the
    ``include`` key, *and* the component sections — is updated in place with
    tomlkit, so comments, key order and array layout survive. Only keys whose
    values actually changed are rewritten.

    This previously rebuilt component sections from ``_dump()`` on the grounds
    that "comment preservation inside repeated-table arrays is impractical with
    tomlkit". It isn't: tomlkit round-trips an AoT byte-for-byte and appends a
    new one without disturbing its neighbours. But component sections are where
    the prose lives — a manifest documents *why* a component links what it
    links — so re-dumping them meant one ``jm warning`` stripped every comment
    from every ``objects/*.toml`` in the project (see gh-491, found in doppler:
    49 files, all prose gone, as a side effect of declaring one warning).

    Falls back silently to plain ``_dump()`` if tomlkit is not installed
    (just-buildit does not propagate ``[project].dependencies`` to the wheel,
    so tomlkit may be absent in tool-installed environments).

    For brand-new files the output is byte-identical to plain ``_dump()`` —
    there is nothing to preserve, and the whole test suite pins that text.
    """

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

    doc = _tk.loads(path.read_text(encoding="utf-8"))

    # -- include list ---------------------------------------------------------
    if include_list is not None:
        if "include" not in doc or doc["include"] != include_list:
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
        _sync(doc["project"], new_proj, _tk)
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
            _sync(mod_tbl[mod], data, _tk)
        for mod in list(mod_tbl.keys()):
            if mod not in new_mod:
                del mod_tbl[mod]
        if not new_mod:
            del doc["module"]

    # -- component sections (gh-491) ------------------------------------------
    # Synced in place, not stripped and re-dumped. This is where the prose
    # lives — a manifest documents *why* a component links what it links — and
    # re-dumping deleted all of it. An unchanged component is not touched at
    # all, so the common case (declare one warning on one object) leaves every
    # other component's text byte-identical.
    new_comps = {
        k: v
        for k, v in cfg.items()
        if k not in ("project", "module", "include")
    }
    for comp, data in new_comps.items():
        if not isinstance(data, dict):
            # Not every top-level key is a table: `enum` is a top-level AoT
            # ([[enum]] name/values), so it is assigned rather than synced.
            if comp not in doc or doc[comp] != data:
                doc[comp] = _tk_value(data, _tk)
            continue
        if comp not in doc:
            doc.add(comp, _tk.table())
        _sync(doc[comp], data, _tk)
    for k in list(doc.keys()):
        if k not in ("project", "module", "include") and k not in new_comps:
            del doc[k]

    # The whole document now round-trips through tomlkit — there is no longer a
    # _dump()-generated body to staple onto a preserved header.
    path.write_text(_tk.dumps(doc).rstrip("\n") + "\n", encoding="utf-8")


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
        if key in ("project", "module", "include", "app", "enum"):
            continue  # `app`/`enum`, like `project`, always live in the manifest
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
    if cfg.get("enum"):
        manifest_content["enum"] = cfg["enum"]  # [[enum]] SSOT, manifest-owned
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
    return [k for k in cfg if k not in ("project", "module", "app", "enum")]


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


def is_serializable(cfg: dict, component: str) -> bool:
    """Return True if the component exposes a serializable-state triplet.

    The C core is assumed to provide (hand-written, sibling to reset):

        size_t <comp>_state_bytes(const <comp>_state_t *);
        void   <comp>_get_state(const <comp>_state_t *, void *blob);
        int    <comp>_set_state(<comp>_state_t *, const void *blob);

    jm then generates the Python binding (state_bytes/get_state/set_state) and
    a uniform round-trip CI test — the "elastic / pure-transducer" face.

    Works for both an object (top-level ``cfg[component]``) and a
    ``kind="handle"`` module (``cfg["module"][component]``, gh-403); the two
    namespaces never collide, so checking both is unambiguous.
    """
    if _truthy(cfg.get(component, {}).get("serializable")):
        return True
    return _truthy(
        cfg.get("module", {}).get(component, {}).get("serializable")
    )


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


def module_kind(cfg: dict, module: str) -> str | None:
    """Return the module's ``kind`` discriminant, if declared.

    ``kind = "capsule"`` selects the capsule generator: a module that exposes
    free functions over an opaque ``PyCapsule`` state (create / execute / reset
    / destroy / get_/set_) rather than a ``PyTypeObject`` per object (gh-286).
    A plain object-group module has no ``kind``."""
    return cfg.get("module", {}).get(module, {}).get("kind")


def is_capsule_module(cfg: dict, module: str) -> bool:
    """Return True if the module is a generated capsule extension (gh-286)."""
    return module_kind(cfg, module) == "capsule"


def capsule_backing(cfg: dict, module: str) -> str:
    """Return a capsule module's ``backing`` symbol prefix.

    The generated free functions wrap ``<backing>_state_t`` and call
    ``<backing>_create`` / ``<backing>_destroy`` / the declared methods, and
    are themselves named ``<backing>_create`` / ``<backing>_execute`` / …. For
    ``ddc_fn`` this is ``"ddcr"`` (→ ``ddcr_state_t``, ``ddcr_create``, …)."""
    return cfg.get("module", {}).get(module, {}).get("backing", "")


def capsule_name(cfg: dict, module: str) -> str:
    """Return a capsule module's ``PyCapsule`` name string.

    Defaults to ``<package>.<module>.<backing>_state`` when unset; an explicit
    ``capsule_name`` (e.g. ``"doppler.ddc.ddcr_state"``) overrides it."""
    return cfg.get("module", {}).get(module, {}).get("capsule_name", "")


def module_methods(cfg: dict, module: str) -> list[dict]:
    """Return a capsule module's declared ``[[module.X.methods]]`` (gh-286).

    Each is ``{name, arg_type?, return_type?, caller_out?, nogil?}``; ``create``
    and ``destroy`` are implicit and not listed."""
    return list(cfg.get("module", {}).get(module, {}).get("methods", []))


def module_properties(cfg: dict, module: str) -> list[dict]:
    """Return a capsule module's declared ``[[module.X.properties]]`` (gh-286).

    Each is ``{name, type, writable?}`` and yields a ``<backing>_get_<name>``
    free function (plus ``<backing>_set_<name>`` when ``writable``)."""
    return list(cfg.get("module", {}).get(module, {}).get("properties", []))


def module_init_params(cfg: dict, module: str) -> list[tuple]:
    """Return a capsule module's create() ``[[module.X.init_params]]`` as
    ``(name, type, default)`` triples — the args of ``<backing>_create``."""
    return [
        (p["name"], p["type"], p.get("default", ""))
        for p in cfg.get("module", {}).get(module, {}).get("init_params", [])
    ]


def capsule_package(cfg: dict, module: str) -> str:
    """Return the package directory a capsule module's ``.so`` / ``.pyi`` land in.

    A capsule module has no ``PyTypeObject`` and frequently lives *inside* a
    sibling object-group package rather than its own — doppler's ``ddc_fn``
    extension is built into the ``ddc`` package so ``doppler.ddc`` can re-export
    its free functions (``from .ddc_fn import ddcr_create``). Declare it with::

        [module.ddc_fn]
        kind    = "capsule"
        package = "ddc"

    When unset the module's own ``pypath`` is used, so a standalone capsule
    module (``doppler.<module>``) needs no key."""
    return cfg.get("module", {}).get(module, {}).get("package", "")


def capsule_header(cfg: dict, module: str) -> str:
    """Return the backing C API header a capsule binding must include.

    Defaults to ``<backing>/<backing>_core.h``; override when the backing
    object's public API is declared elsewhere — ``ddcr``'s lifecycle lives in
    ``ddc/ddc_core.h``, so ``ddc_fn`` sets ``header = "ddc/ddc_core.h"``."""
    return cfg.get("module", {}).get(module, {}).get("header", "")


def capsule_depends_on(cfg: dict, module: str) -> list:
    """Raw ``depends_on`` entries for a capsule module (strings / ``{name,link}``
    tables). The ``link = true`` cores are linked onto the generated ``.so``."""
    return list(cfg.get("module", {}).get(module, {}).get("depends_on", []))


def capsule_extra_link_libs(cfg: dict, module: str) -> list[str]:
    """Non-component link targets for a capsule ``.so`` (e.g. ``["m"]``)."""
    v = cfg.get("module", {}).get(module, {}).get("extra_link_libs", [])
    return list(v) if isinstance(v, (list, tuple)) else []


def is_composer_module(cfg: dict, module: str) -> bool:
    """Return True if the module is a generated *composer* extension (gh-287).

    A composer module is built on the capsule skeleton (gh-286) but adds a
    multi-source / segment / timeline composition data model and CPython OO
    types. It declares ``kind = "composer"`` and a set of sub-tables
    (``source`` / ``segment`` / ``timeline`` / ``oo`` / ``json``) describing how
    one or more ``generator`` source objects are summed, sequenced, and
    serialized. Subsumes doppler's hand-written ``wfmcompose_py`` extension."""
    return module_kind(cfg, module) == "composer"


def composer_composes(cfg: dict, module: str) -> list[str]:
    """Return the ``generator`` source objects a composer reuses (gh-287).

    Each name is an existing object whose ``init_params`` define a source's
    synth configuration — the composer's ``source`` table layers the
    composition-only fields (level, snr, enums, bits) on top. doppler's
    ``wfm_compose`` composes ``["wfm_synth"]``."""
    v = cfg.get("module", {}).get(module, {}).get("composes", [])
    return list(v) if isinstance(v, (list, tuple)) else []


def composer_sample_type(cfg: dict, module: str) -> bool:
    """Return True if a composer turns on the jm-app output axes (gh-287).

    When set, the generated CLI faces inherit ``--sample_type`` /
    ``--file-type`` / ``--endian`` / ``--record`` from ``jm app`` verbatim
    (``sample_type/file_type/endian`` stay owned by ``jm app`` — never
    re-declared in the enum SSOT)."""
    return _truthy(cfg.get("module", {}).get(module, {}).get("sample_type"))


def composer_source(cfg: dict, module: str) -> dict:
    """Return the composer ``[module.X.source]`` table (gh-287).

    Describes the composition-only fields layered on the composed generator's
    ``init_params`` — exactly the data ``wfm_source_t`` carries beyond
    ``wfm_synth_create()`` (wfm_compose.h:49). Keys:

    ``object``       the composed generator object name (e.g. ``"wfm_synth"``);
    ``enum_fields``  ``{field: enum_name}`` — int fields serialized as strings
                     via the ``[[enum]]`` SSOT (gh-285);
    ``extra_fields`` ``[{name, type, default?}, …]`` — scalar add-ons (level, snr);
    ``bytes_field``  ``{name, c, len}`` — an owned byte buffer (``bits``).

    A ``[[….source.fields]]`` entry also accepts an optional ``doc`` string,
    rendered as the field's numpy-parameter description in the ``.pyi``. A field
    with ``complex = true`` (C type ``float _Complex*``) takes a numpy complex64
    array, stored as an owned ``src-><name>`` / ``src->n_<name>`` pair — the
    complex analog of a ``bytes`` field (excluded from the generic JSON/CLI; it
    crosses via the getset or a ``to_json_fn``)."""
    return dict(cfg.get("module", {}).get(module, {}).get("source", {}))


def composer_segment(cfg: dict, module: str) -> dict:
    """Return the composer ``[module.X.segment]`` table (gh-287).

    Maps 1:1 to ``wfm_segment_t`` (wfm_compose.h:77). Keys: ``fields``
    (``[{name, type, default?}, …]`` — fs / num_samples / off_samples),
    ``sources`` (``"multi"`` to sum N sources per segment, ``"single"`` for
    one), and ``flat_sources`` (``true`` to proxy a single-source segment's
    source fields as read-only attributes — ``segment.freq`` reads
    ``segment.sources[0].freq``; gh-287 round 3 feature 4)."""
    return dict(cfg.get("module", {}).get(module, {}).get("segment", {}))


def composer_timeline(cfg: dict, module: str) -> dict:
    """Return the composer ``[module.X.timeline]`` table (gh-287).

    Keys: ``loop`` — the ordered loop modes (e.g. ``["once","repeat",
    "continuous"]``) mapping to the ``repeat`` / ``continuous`` create flags."""
    return dict(cfg.get("module", {}).get(module, {}).get("timeline", {}))


def composer_oo(cfg: dict, module: str) -> dict:
    """Return the composer ``[module.X.oo]`` table (gh-287).

    Keys: ``factories`` (the convenience constructors — ``tone`` / ``bpsk`` /
    … — exposed alongside the generated CPython types) and ``emit`` (``"ctypes"``
    to emit mutable ``PyTypeObject``s in the ``.so``, the default and only
    supported mode — the ergonomics live in the extension, not pure Python)."""
    return dict(cfg.get("module", {}).get(module, {}).get("oo", {}))


def composer_stream(cfg: dict, module: str) -> dict:
    """Return the composer ``[module.X.composer]`` table (gh-287 round 3).

    Composer-level ergonomics generated into the ``.so``. Keys:

    ``stream``    ``true`` to generate a ``<Composer>.stream(block=4096)``
                  method returning an iterator that drains ``execute`` (the
                  ``for blk in c.stream(n):`` convenience);
    ``to_dict``   ``true`` to generate a ``<Composer>.to_dict()`` returning the
                  resolved composition (``repeat`` / ``continuous`` / nested
                  ``segments``) as a plain dict — the generic introspection
                  primitive any sidecar metadata (SigMF, BLUE, …) is built from
                  in Python, so jm generates none of those formats itself."""
    return dict(cfg.get("module", {}).get(module, {}).get("composer", {}))


def composer_json(cfg: dict, module: str) -> bool:
    """Return True if a composer generates ``to_json`` / ``from_json`` (gh-287).

    The JSON shape is derived from ``source.fields`` / ``segment.fields`` and
    the ``[[enum]]`` SSOT (enums serialize as strings), so the recorded spec
    round-trips byte-for-byte (wfm_compose.h:143)."""
    return _truthy(
        cfg.get("module", {}).get(module, {}).get("json", {}).get("enabled")
    )


def composer_serializers(cfg: dict, module: str) -> list[dict]:
    """Return a composer's ``[[module.X.serializers]]`` — additional **delegated**
    serializers (gh-317). Each is ``{name, fn, returns?, params?[]}`` and emits a
    ``<Composer>.<name>(<params>) -> <returns>`` method that calls the C
    ``fn(<params>, segs, n)`` over the composer's resolved segments.

    This is the sanctioned mechanism for **domain wire formats** jm generates
    none of (SigMF, BLUE, …): the project hand-writes the C serializer, jm
    generates the typed method over the resolved spec (gh-313 — the delegation
    is a deliberate carve-out, not drift). Each param is ``{name, type, enum?,
    default?}`` (enums cross as validated SSOT strings).

    An optional ``header`` key (gh-343) is ``#include``-d in the generated
    ``<module>_ext.c`` so the serializer ``fn``'s declaration is in scope — the
    fn lives in an arbitrary project header, not an auto-included
    ``<dep>_core.h``, so without it the call is an implicit declaration that
    miscompiles."""
    v = cfg.get("module", {}).get(module, {}).get("serializers", [])
    return list(v) if isinstance(v, (list, tuple)) else []


def functions_in_core(cfg: dict, module: str) -> bool:
    """Return True if the module's free functions live in ``<module>_core.c``
    as one translation unit, rather than one ``.c`` per function (gh-247).

    Off by default (each function gets its own sacred ``<fn>.c``). When on,
    ``jm function`` appends each stub to the shared ``<module>_core.c`` — so
    `static` helpers can be shared and the module is one TU — and the
    CMakeLists lists only ``<module>_core.c``."""
    return _truthy(
        cfg.get("module", {}).get(module, {}).get("functions_in_core")
    )


# ── handle modules (gh-306) ──────────────────────────────────────────────────
#
# A ``kind = "handle"`` module is the *intersection* of the capsule generator
# (gh-286 — opaque hand-C backing + lifecycle + numpy marshaling) and the
# composer generator (gh-287 — a typed ``PyTypeObject`` face): it emits one
# CPython class over an OPAQUE hand-C resource handle (e.g. ``Writer`` over
# ``wfm_writer_t``). The accessors below mirror the ``capsule_*`` / ``composer_*``
# drill-down pattern; like every jm reader, unknown keys pass through (no schema
# validation) so the surface is purely additive.


def is_handle_module(cfg: dict, module: str) -> bool:
    """Return True if the module is a generated *handle* extension (gh-306).

    A handle module declares ``kind = "handle"`` and templates the
    capsule-backed typed-resource archetype once: an opaque create → handle, a
    typed ``PyTypeObject`` with methods over the handle, properties decoded from
    a shared C getter, a context-manager / idempotent-close RAII protocol, and
    an optional weak-symbol backend guard."""
    return module_kind(cfg, module) == "handle"


def handle_backing(cfg: dict, module: str) -> str:
    """Return a handle module's ``backing`` symbol prefix.

    The opaque resource type defaults to ``<backing>_t`` and the lifecycle
    functions to ``<backing>_open`` / ``<backing>_close``; ``wfm_writer`` →
    ``wfm_writer_t``, ``wfm_writer_open``, ``wfm_writer_close``, …."""
    return cfg.get("module", {}).get(module, {}).get("backing", "")


def handle_type(cfg: dict, module: str) -> str:
    """Return the opaque C handle type — defaults to ``<backing>_t``.

    The generated struct stores a ``<handle_type> *`` and never introspects it;
    its lifetime is owned by ``create_fn`` / ``close_fn``."""
    backing = handle_backing(cfg, module)
    return (
        cfg.get("module", {})
        .get(module, {})
        .get("handle_type", f"{backing}_t" if backing else "")
    )


def handle_type_name(cfg: dict, module: str) -> str:
    """Return the Python class name the handle module registers (``"Writer"``).

    Defaults to the ``type_name`` key; falls back to the CamelCased backing."""
    tn = cfg.get("module", {}).get(module, {}).get("type_name")
    if tn:
        return tn
    backing = handle_backing(cfg, module)
    return (
        "".join(p.capitalize() for p in backing.split("_")) if backing else ""
    )


def handle_create_fn(cfg: dict, module: str) -> str:
    """Return the C constructor a handle's ``tp_init`` calls.

    Coerces the declared ``create_args`` and calls
    ``<handle_type> *create_fn(...)``; a NULL return becomes a ``tp_init``
    error. Defaults to ``<backing>_open``."""
    backing = handle_backing(cfg, module)
    return (
        cfg.get("module", {})
        .get(module, {})
        .get("create_fn", f"{backing}_open" if backing else "")
    )


def handle_init_fn(cfg: dict, module: str) -> str:
    """Return a handle's **init-in-place** constructor, if any (gh-315).

    An alternative to ``create_fn``: instead of ``T *create_fn(args)`` that
    allocates and returns the handle, ``void init_fn(T *obj, args…)`` inits a
    caller-allocated struct. When set, ``tp_init`` mallocs ``sizeof(<handle_type>)``,
    calls ``init_fn(self->h, args…)``, and ``close``/``tp_dealloc`` ``free`` it
    (after an optional ``close_fn`` that finalizes owned members). Mutually
    exclusive with ``create_fn``; unset by default."""
    return cfg.get("module", {}).get(module, {}).get("init_fn", "")


def handle_create_args(cfg: dict, module: str) -> list[dict]:
    """Return a handle's ``[[module.X.create_args]]`` — the ``create_fn`` args.

    Each is ``{name, type, enum?, default?, kwonly?}``. ``type`` is a scalar C
    type (coerced via :data:`_types._CTYPE_META`), ``"path"`` (an ``os.fspath``
    coercion crossing as ``O&`` + ``PyUnicode_FSConverter``), or carries an
    ``enum`` (a string parsed to the C int via the ``[[enum]]`` SSOT)."""
    return list(cfg.get("module", {}).get(module, {}).get("create_args", []))


def handle_create_post(cfg: dict, module: str) -> list[dict]:
    """Return a handle's ``[[module.X.create_post]]`` post-create setters.

    Each is ``{fn, when?, arg?}`` — a C call run after ``create_fn`` succeeds,
    optionally guarded by a truthy init arg (``when = "headroom"``) and passed a
    verbatim-C argument expression (``arg = "pow(10, -headroom/20)"``)."""
    return list(cfg.get("module", {}).get(module, {}).get("create_post", []))


def handle_methods(cfg: dict, module: str) -> list[dict]:
    """Return a handle's ``[[module.X.methods]]`` — handle methods.

    Each is ``{name, fn, args?, returns?, nogil?}`` and yields a
    ``tp_methods`` entry calling ``fn(self->h, …)``. ``args`` are scalars or one
    array-in (marshaled like the capsule numpy path); ``returns`` is a scalar
    type or an array form (``"int_in -> array"``)."""
    return list(cfg.get("module", {}).get(module, {}).get("methods", []))


def handle_getters(cfg: dict, module: str) -> list[dict]:
    """Return a handle's ``[[module.X.getters]]`` — decoded-getter properties.

    Each is ``{fn, out, cache?, fields:[…]}``: one C getter ``fn(self->h, &tmp)``
    fills an ``out``-typed struct, and each declared field decodes one property
    from it. A field is ``{name, from?, type, enum?, scale?, expr?}`` — plain
    (``_to_py(tmp.<from|name>)``), ``enum`` (index → string), ``scale``
    (multiply), or ``expr`` (verbatim C over ``tmp.<f>`` + stashed inits).
    ``cache = true`` resolves the getter once in ``tp_init`` (fixed metadata)."""
    return list(cfg.get("module", {}).get(module, {}).get("getters", []))


def handle_context(cfg: dict, module: str) -> bool:
    """Return True if the handle generates the context-manager protocol.

    ``__enter__`` returns ``self``; ``__exit__`` calls ``close``. Independent of
    the always-generated idempotent ``close`` + ``tp_dealloc``."""
    return _truthy(
        cfg.get("module", {}).get(module, {}).get("context_manager")
    )


def handle_close_fn(cfg: dict, module: str) -> str:
    """Return the idempotent destructor a handle's ``close`` / ``tp_dealloc``
    call. Defaults to ``<backing>_close``."""
    backing = handle_backing(cfg, module)
    return (
        cfg.get("module", {})
        .get(module, {})
        .get("close_fn", f"{backing}_close" if backing else "")
    )


def handle_close_returns(cfg: dict, module: str) -> str:
    """Return the C return type of a handle's ``close_fn``, if it reports a
    status code (gh-178 review #5).

    Defaults to ``""`` — ``close_fn`` is treated as ``void`` and its result is
    ignored, matching most destructors. When set (``close_returns = "int"``),
    the generated ``close()`` captures the return value and raises
    ``RuntimeError`` on a non-zero code (``wfm_writer_close`` patches the BLUE
    header on close and can fail on a short write). ``tp_dealloc`` and the
    re-``__init__`` teardown still ignore it — neither may raise."""
    return cfg.get("module", {}).get(module, {}).get("close_returns", "")


def handle_optional_backend(cfg: dict, module: str) -> str:
    """Return the weak symbol a handle's ``tp_init`` guards on, if any.

    When set (``optional_backend = "wfm_zmq_sink_open"``), the backing is
    declared as a weak extern and ``tp_init`` raises ``NotImplementedError`` when
    the symbol resolves to NULL — the "not on this platform" path."""
    return cfg.get("module", {}).get(module, {}).get("optional_backend", "")


# The package / header / depends_on / extra_link_libs keys behave exactly as
# their capsule twins; expose handle-named aliases so the generator reads a
# consistent ``handle_*`` surface (and a future schema split stays cheap).


def handle_package(cfg: dict, module: str) -> str:
    """Package directory a handle module's ``.so`` / ``.pyi`` land in.

    Like a capsule module, a handle frequently lives *inside* a sibling package
    (doppler's ``Writer`` lands in the ``wfm`` package). Defaults to the
    module's own ``pypath`` when unset. See :func:`capsule_package`."""
    return capsule_package(cfg, module)


def handle_header(cfg: dict, module: str) -> str:
    """Backing C API header a handle binding must include.

    Defaults to ``<backing>/<backing>_core.h`` (filled by the generator). See
    :func:`capsule_header`."""
    return capsule_header(cfg, module)


def handle_depends_on(cfg: dict, module: str) -> list:
    """Raw ``depends_on`` entries for a handle module; ``link = true`` cores are
    linked onto the generated ``.so``. See :func:`capsule_depends_on`."""
    return capsule_depends_on(cfg, module)


def handle_extra_link_libs(cfg: dict, module: str) -> list[str]:
    """Non-component link targets for a handle ``.so`` (e.g. ``["m"]``).
    See :func:`capsule_extra_link_libs`."""
    return capsule_extra_link_libs(cfg, module)


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


def enums(cfg: dict) -> dict[str, list[str]]:
    """Return the project's named string-enums, ``{name: [values, …]}``.

    Declared once at the top level as a single-source-of-truth so the same
    ordered value set feeds every face (C choice tables, JSON, choice flags,
    bindings) instead of being re-spelled per parameter:

    .. code-block:: toml

        [[enum]]
        name = "wfm_type"
        values = ["tone", "noise", "pn", "bpsk", "qpsk", "chirp", "bits"]

    Value order *is* the C integer value — append-only, never reorder. A
    parameter refers to one with ``type = "enum:wfm_type"`` (see
    :func:`resolve_enum_type`)."""
    out: dict[str, list[str]] = {}
    for e in cfg.get("enum", []):
        name = e.get("name")
        if name:
            out[name] = list(e.get("values", []))
    return out


def resolve_enum_type(cfg: dict, ptype: str) -> str:
    """Expand an ``enum:<name>`` reference to its ``string_enum:`` spec.

    Returns *ptype* unchanged when it is not an enum reference, so this is safe
    to apply to every parameter type. Resolution happens only on the codegen
    read path (e.g. :func:`init_params`); the manifest keeps the ``enum:``
    reference on disk. Raises ``ValueError`` for an undefined enum name."""
    if not ptype.startswith("enum:"):
        return ptype
    name = ptype[len("enum:") :]
    registry = enums(cfg)
    if name not in registry:
        known = ", ".join(sorted(registry)) or "(none declared)"
        raise ValueError(
            f"parameter type '{ptype}' references an undefined [[enum]] "
            f"'{name}'; declared enums: {known}"
        )
    return "string_enum:" + ",".join(registry[name])


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


def transitive_dep_cores(
    cfg: dict, entries: list, link_only: bool = False
) -> list[str]:
    """Deduped, direct-first closure of ``<name>_core`` link targets reachable
    from *entries* over the ``depends_on`` graph (gh-280).

    CMake OBJECT libraries don't propagate their objects through transitive
    PUBLIC linking, so every core a `test_<obj>_core` / `bench_<obj>_core` (and
    the module ``.so``) ultimately pulls in must appear **directly** on its link
    line. Rather than make each object hand-list the full closure on its
    ``depends_on`` (redundant, and silently stale when a level is inserted), jm
    walks the graph and emits the closure itself — so an object declares only
    its *direct* deps.

    *entries* is a raw ``depends_on`` list (the consuming object need not be in
    *cfg* yet — `_init.run` renders before persisting); each dependency's own
    ``depends_on`` is read from *cfg* by stripping the ``_core`` suffix to its
    component key. ``link_only`` follows / emits only ``link = true`` edges (the
    ``.so`` and standalone paths); otherwise every ``depends_on`` edge counts
    (the module per-object paths, which link all declared cores). The walk is
    cycle-guarded via the dedupe set."""

    def _cores(ents):
        if link_only:
            return dep_link_libs(ents)
        return [
            (n[:-5] if n.endswith("_core") else n) + "_core"
            for n in dep_names(ents)
        ]

    out: list[str] = []
    seen: set[str] = set()

    def _visit(ents):
        for core in _cores(ents):
            if core in seen:
                continue
            seen.add(core)
            out.append(core)
            _visit(depends_on_raw(cfg, core[:-5]))

    _visit(entries)
    return out


def array_args(cfg: dict, component: str) -> list[tuple[str, str]]:
    """Return declared array constructor args for component as [(name, dtype), ...]."""
    return [
        (a["name"], a.get("type") or a.get("dtype", ""))
        for a in cfg.get(component, {}).get("array_args", [])
    ]


def init_params(cfg: dict, component: str) -> list[tuple]:
    """Return --init-param entries as 10-tuples.

    ``(name, type, default, default_raw, real_type, real_create_fn, optional,
    create_fn, required, doc)``

    ``default_raw`` overrides the type's parse_zero for the raw C variable.
    ``real_type`` / ``real_create_fn`` enable dtype-dispatch: when the array
    arrives as the ``real_type`` numpy dtype, ``real_create_fn`` is called
    instead of the default ``<component>_create``.
    ``optional`` / ``create_fn`` enable optional-array dispatch: when the
    caller supplies the array kwarg, ``create_fn`` is called instead of
    ``<component>_create``; when omitted, ``<component>_create`` is called
    with only the scalar params.  ``required`` (gh-266) marks a *scalar* param
    mandatory: it parses as a positional before the PyArg ``|`` so omitting it
    raises ``TypeError`` at the call boundary instead of passing the type's
    zero through to a constructor that returns NULL.  ``doc`` is the optional
    manifest override for the constructor-parameter description; when empty the
    docstring generators fall back to the create function's ``@param`` and then
    a stub.  All fields default to ``""`` / ``False`` when absent.  Callers may
    unpack defensively with ``param[:3]``.
    """
    return _project_init_params(
        cfg, cfg.get(component, {}).get("init_params", [])
    )


def _project_init_params(cfg: dict, param_dicts: list[dict]) -> list[tuple]:
    """Project a list of init-param dicts to the 10-tuple form.

    Shared by `init_params` (the object path) and the view path (gh-504), so a
    view's own ``init_params`` becomes the same tuple shape ``make_state_ctx``
    already consumes.
    """
    return [
        (
            p["name"],
            resolve_enum_type(cfg, p["type"]),
            p.get("default", ""),
            p.get("default_raw", ""),
            p.get("real_type", ""),
            p.get("real_create_fn", ""),
            p.get("optional", False),
            p.get("create_fn", ""),
            p.get("required", False),
            p.get("doc", ""),
        )
        for p in param_dicts
    ]


def init_param_tuple_to_dict(p: tuple) -> dict:
    """Convert one parsed init-param tuple to its stored-manifest dict shape.

    Shared by `add_component` and the `jm view` generator so both persist an
    identical ``init_params`` record (gh-504). Accepts the 3-to-10-field
    tuples `parse_init_param_flag` and callers produce.
    """
    n, t, d = p[:3]
    rec: dict = {"name": n, "type": t}
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
    if len(p) > 8 and p[8]:
        rec["required"] = True
    if len(p) > 9 and p[9]:
        rec["doc"] = p[9]
    return rec


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


def param_headers(cfg: dict, component: str) -> list[str]:
    """Headers declared on method params (gh-432), deduped, declaration order.

    A capsule-typed param's foreign C type may live in a header outside the
    ``<dep>/<dep>_core.h`` convention (e.g. ``telemetry/telemetry.h``); the
    per-param ``header`` key names it and this collects every such header for
    the component so the include-injection paths can reach it.
    """
    out: list[str] = []
    for m in methods(cfg, component):
        for p in m.get("params") or []:
            h = p.get("header")
            if h and h not in out:
                out.append(h)
    return out


def properties(cfg: dict, component: str) -> list[dict]:
    """Return declared Python properties for component (empty list if none)."""
    return list(cfg.get(component, {}).get("properties", []))


def add_property(cfg: dict, component: str, prop: dict) -> dict:
    """Append a property entry to the component's properties list."""
    cfg.setdefault(component, {}).setdefault("properties", []).append(prop)
    return cfg


def views(cfg: dict, component: str) -> list[dict]:
    """Return declared views for component (gh-504; [] if none).

    A *view* is a second Python class over the same generated C core: it
    shares ``<component>_state_t`` and the parent's ``_core.c``, differing
    only in its ``class_name``, its C constructor (``create_fn``), its own
    ``init_params``, and an optionally-trimmed property surface
    (``exclude_properties``). Each entry carries ``class_name`` and
    ``create_fn`` (both required), plus optional ``init_params`` (the view's
    own constructor shape; falls back to the parent's) and
    ``exclude_properties`` (a list of parent property names to omit). Views
    are a module-object feature only.
    """
    return list(cfg.get(component, {}).get("views", []))


def add_view(cfg: dict, component: str, view: dict) -> dict:
    """Append a view entry to the component's views list (gh-504)."""
    cfg.setdefault(component, {}).setdefault("views", []).append(view)
    return cfg


def view_init_params(cfg: dict, component: str, view: dict) -> list[tuple]:
    """10-tuple init_params for a view: its own if declared, else parent's.

    A view whose ``create_fn`` takes the same arguments as the parent omits
    ``init_params`` and inherits the parent's constructor shape; one whose
    constructor differs declares its own.
    """
    own = view.get("init_params")
    if own:
        return _project_init_params(cfg, own)
    return init_params(cfg, component)


def view_exclude_properties(view: dict) -> set[str]:
    """Parent property names a view omits from its Python surface (gh-504)."""
    return set(view.get("exclude_properties", []))


def view_exclude_methods(view: dict) -> set[str]:
    """Parent method names a view omits from its Python surface (gh-504).

    The shared C function stays; only the view's Python-facing wrapper and its
    ``PyMethodDef`` entry are dropped, so there is no dangling symbol. Builtins
    (``step``/``steps``/``reset``) are not ``[[<comp>.methods]]`` entries and so
    are not excludable.
    """
    return set(view.get("exclude_methods", []))


def view_properties(view: dict) -> list[dict]:
    """A view's OWN properties (gh-504): entries that ADD a property the parent
    lacks, or OVERRIDE a parent property of the same name (e.g. a different
    doc). Merged over the parent's in ``_make_view_ctx``. [] if none."""
    return list(view.get("properties", []))


def view_methods(view: dict) -> list[dict]:
    """A view's OWN methods (gh-504): ADD a new method (scaffolds a shared C
    stub) or OVERRIDE a parent method's doc. [] if none."""
    return list(view.get("methods", []))


def view_warnings(view: dict) -> list[dict]:
    """A view's OWN post-construction warnings (gh-509; [] if none).

    Same shape as ``[[<comp>.warnings]]`` (gh-481) but scoped to one view, so
    a second front door over the shared core (e.g. ``BurstAcquisition``) can
    surface its own ``PyErr_WarnEx`` on a bool field of the shared state
    struct — the view carries no parent warnings of its own, so this is the
    only source for its ``init_warn_block``.
    """
    return list(view.get("warnings", []))


def add_view_warning(
    cfg: dict, component: str, class_name: str, warning: dict
) -> dict:
    """Add/replace a warning on the named view (gh-509).

    Idempotent on ``(condition, after)`` — a re-declared warning replaces the
    existing entry rather than emitting a duplicate ``if`` guard, matching
    ``_warning.run``'s object-level behaviour so ``jm apply`` replay is stable.
    """
    view = _find_view(cfg, component, class_name)
    if view is None:
        raise KeyError(f"no view {class_name!r} on {component!r}")
    existing = view.setdefault("warnings", [])
    for i, w in enumerate(existing):
        if w.get("condition") == warning["condition"] and w.get(
            "after", "__init__"
        ) == warning.get("after", "__init__"):
            existing[i] = warning
            return cfg
    existing.append(warning)
    return cfg


def _find_view(cfg: dict, component: str, class_name: str) -> dict | None:
    """The view entry on *component* whose ``class_name`` matches, else None."""
    for v in views(cfg, component):
        if v.get("class_name") == class_name:
            return v
    return None


def add_view_property(
    cfg: dict, component: str, class_name: str, prop: dict
) -> dict:
    """Append a property to the named view's own list (gh-504)."""
    view = _find_view(cfg, component, class_name)
    if view is None:
        raise KeyError(f"no view '{class_name}' on object '{component}'")
    view.setdefault("properties", []).append(prop)
    return cfg


def add_view_method(
    cfg: dict, component: str, class_name: str, method: dict
) -> dict:
    """Append a method to the named view's own list (gh-504)."""
    view = _find_view(cfg, component, class_name)
    if view is None:
        raise KeyError(f"no view '{class_name}' on object '{component}'")
    view.setdefault("methods", []).append(method)
    return cfg


# Python's built-in warning categories (gh-481). Each name maps 1:1 onto a
# ``PyExc_<name>`` symbol in the C API, so the codegen interpolates the name
# directly. This frozenset is what keeps a typo out of the generated C, where
# it would surface as an undeclared-identifier compile error in the user's
# build rather than as a jm diagnostic.
WARNING_CATEGORIES = frozenset(
    {
        "Warning",
        "UserWarning",
        "DeprecationWarning",
        "PendingDeprecationWarning",
        "RuntimeWarning",
        "FutureWarning",
        "SyntaxWarning",
        "ImportWarning",
        "UnicodeWarning",
        "BytesWarning",
        "ResourceWarning",
    }
)


def warnings(cfg: dict, component: str) -> list[dict]:
    """Return declared post-construction warnings (gh-481; [] if none).

    Each entry carries ``after`` (today always ``__init__``), ``condition``
    (a bool-valued field on the state struct), ``category`` (a name from
    `WARNING_CATEGORIES`) and ``message``.
    """
    return list(cfg.get(component, {}).get("warnings", []))


def add_warning(cfg: dict, component: str, warning: dict) -> dict:
    """Append a warning entry to the component's warnings list."""
    cfg.setdefault(component, {}).setdefault("warnings", []).append(warning)
    return cfg


# Python exception classes a create() failure may be reported as (gh-482).
# Same 1:1 ``PyExc_<name>`` mapping as `WARNING_CATEGORIES`, and the same
# reason for existing: a typo here would otherwise reach the user's compiler
# as an undeclared identifier rather than a jm diagnostic.
ERROR_CATEGORIES = frozenset(
    {
        "Exception",
        "ValueError",
        "TypeError",
        "RuntimeError",
        "MemoryError",
        "OverflowError",
        "ArithmeticError",
        "ZeroDivisionError",
        "FloatingPointError",
        "IndexError",
        "KeyError",
        "BufferError",
        "NotImplementedError",
        "OSError",
    }
)


def create_error(cfg: dict, component: str) -> str:
    """Exception class for a ``create()`` failure (gh-482; "" if undeclared).

    Undeclared means the generated glue keeps its historical blanket
    ``MemoryError``, so existing projects are untouched.
    """
    return cfg.get(component, {}).get("create_error", "")


def create_error_message(cfg: dict, component: str) -> str:
    """Message paired with `create_error` ("" if undeclared)."""
    return cfg.get(component, {}).get("create_error_message", "")


def set_create_error(
    cfg: dict, component: str, category: str, message: str
) -> dict:
    """Declare how this component's ``create()`` failure reports to Python.

    Scalars rather than a table array (contrast ``[[<comp>.warnings]]``):
    ``create()`` has exactly one failure channel — a NULL return — so there is
    exactly one translation to declare. See `_context._diagnostics` for why
    that channel can't distinguish reasons without changing the C API.
    """
    cfg.setdefault(component, {})["create_error"] = category
    cfg[component]["create_error_message"] = message
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


def controllable_state_vars(
    cfg: dict, component: str
) -> list[tuple[str, str]]:
    """Non-opaque state entries flagged ``controllable = true``.

    A controllable state var becomes an optional, keyword-capable per-call
    override on the object's ``steps()``: ``obj.steps(x, gain=2.0)`` uses the
    supplied value for that block, while omitting it reads ``self->gain``.
    The override is non-persistent (it does not mutate the field).  Returns
    ``(name, ctype)`` pairs in declaration order — the order in which the C
    ``<comp>_steps()`` signature and the kwlist append them."""
    return [
        (s["name"], s["type"])
        for s in cfg.get(component, {}).get("state", [])
        if s.get("controllable") and not s.get("opaque")
    ]


def controllable_names(cfg: dict, component: str) -> frozenset[str]:
    """Names of state entries flagged ``controllable = true``.

    The round-trip key threaded through generation so ``jm apply`` /
    ``jm regenerate`` re-persist the flag (mirrors :func:`no_ctor_names`)."""
    return frozenset(n for n, _ in controllable_state_vars(cfg, component))


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


_DEFAULT_BENCH_BLOCK_SIZES = [1024, 65536]


def project_bench_block_sizes(cfg: dict) -> list[int]:
    """Block sizes for the generated Python benchmarks (``[project.bench]
    block_sizes``).

    Defaults to ``[1024, 65536]`` — the historical ``_1k`` + ``_64k`` suites
    — when unset, so existing scaffolds are byte-identical. A project that
    only benches large blocks declares e.g.::

        [project.bench]
        block_sizes = [65536]

    and ``jm`` stops reintroducing the ``_1k`` suite on every scaffold /
    reconcile. Sizes are de-duplicated, sorted ascending, and any
    non-positive entry is dropped; an empty / malformed value falls back to
    the default. Only the Python ``bench_<obj>.py`` files honour this — the
    C ``bench_<obj>_core.c`` uses a single fixed block and is unaffected.
    """
    v = cfg.get("project", {}).get("bench", {}).get("block_sizes")
    if not isinstance(v, (list, tuple)) or not v:
        return list(_DEFAULT_BENCH_BLOCK_SIZES)
    sizes = sorted({int(n) for n in v if int(n) > 0})
    return sizes or list(_DEFAULT_BENCH_BLOCK_SIZES)


def c_style(cfg: dict) -> str:
    """C-output style declared under ``[project] c_style`` (gh-265).

    Empty (the default) leaves jm's canonical 4-space output untouched. The
    only recognised value today is ``"clang-format"``: jm runs ``clang-format``
    over the generated ``native/**`` C/H after every mutating command, so the
    emitted code already matches the project's committed ``.clang-format``
    instead of forcing a manual reformat pass (doppler's documented friction).
    """
    return str(cfg.get("project", {}).get("c_style", ""))


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


def object_create_fn(cfg: dict, component: str) -> str | None:
    """Return the object's C constructor override, or None for the default.

    gh-509: a plain object may back its ``tp_init`` with a differently named
    C constructor than ``<component>_create`` (e.g. ``acq_create_continuous``,
    where ``acq_create`` does not exist). ``None`` preserves the historical
    ``<component>_create`` default, byte-identical for every existing project.
    The declared function must take the same argument list the init_params
    already generate — this overrides only the name, not the call shape.
    """
    return cfg.get(component, {}).get("create_fn") or None


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
    serializable_: bool = False,
    streamable_: bool = False,
    async_stream_: bool = False,
    stream_block_default_: "int | None" = None,
    init_params_: list[tuple[str, str, str]] = (),
    class_name_: str | None = None,
    depends_on_: list[str] = (),
    opaque_fields_: "list[tuple[str, str]]" = (),
    no_ctor_names_: "frozenset[str]" = frozenset(),
    controllable_names_: "frozenset[str]" = frozenset(),
    extra_link_libs_: list[str] = (),
    extra_include_dirs_: list[str] = (),
    create_fn_: str | None = None,
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
            **({"controllable": True} if n in controllable_names_ else {}),
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
    if serializable_:
        entry["serializable"] = "true"
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
        entry["init_params"] = [
            init_param_tuple_to_dict(p) for p in init_params_
        ]
    if class_name_:
        entry["class_name"] = class_name_
    if create_fn_:
        entry["create_fn"] = create_fn_
    if depends_on_:
        entry["depends_on"] = list(depends_on_)
    if extra_link_libs_:
        entry["extra_link_libs"] = list(extra_link_libs_)
    if extra_include_dirs_:
        entry["extra_include_dirs"] = list(extra_include_dirs_)
    cfg[component] = entry
    return cfg


def _str_assign(key: str, value: str) -> str:
    """Render ``<key> = ...`` for the TOML dump, escaping as needed.

    Multi-line values use a TOML basic multi-line string; single-line values
    use a basic string with quotes/backslashes escaped. Shared by ``doc`` and
    by the ``message`` key on ``[[<comp>.warnings]]`` (gh-481), whose prose is
    authored by a human and routinely contains quotes.
    """
    if "\n" in value:
        # strip("\n") for round-trip idempotency (gh-192) — see _dump impl keys.
        body = (
            value.replace("\\", "\\\\").replace('"""', '\\"\\"\\"').strip("\n")
        )
        return f'{key} = """\n{body}\n"""'
    body = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{key} = "{body}"'


def _doc_assign(value: str) -> str:
    """Render ``doc = ...`` for the TOML dump."""
    return _str_assign("doc", value)


# Method keys the _dump serializer emits explicitly (the list/table ones —
# multi_output/extra_args/params/result_fields — included). Any OTHER scalar
# key authored on a manifest method is round-tripped generically (gh-257) so a
# hand-written key such as `record_name` survives save()->load() instead of
# being silently stripped by the write pass.
_KNOWN_METHOD_KEYS = frozenset(
    {
        "name",
        "doc",
        "arg_type",
        "return_type",
        "varargs",
        "manual_stub",
        "variable_output",
        "pass_capacity",
        "nogil",
        "none_on_empty",
        "batch",
        "multi_output",
        "extra_args",
        "params",
        "out_type",
        "out_divisor",
        "bench",
        "max_results",
        "result_fields",
        "single",
        "py_return_type",
        "max_out",
    }
)


def _inline_field(f: dict) -> str:
    """Serialize a composer field (``{name, type, enum?, default?, bytes?}``) as
    a TOML inline table — drives the source/segment field marshalling (gh-287)."""
    parts = [f'name = "{f["name"]}"', f'type = "{f["type"]}"']
    if f.get("enum"):
        parts.append(f'enum = "{f["enum"]}"')
    if f.get("default") not in (None, ""):
        parts.append(f'default = "{f["default"]}"')
    if f.get("bytes"):
        parts.append("bytes = true")
    if f.get("aliases"):
        parts.append(
            "aliases = [" + ", ".join(f'"{a}"' for a in f["aliases"]) + "]"
        )
    if f.get("coerce"):
        parts.append(f'coerce = "{f["coerce"]}"')
    return "{ " + ", ".join(parts) + " }"


def _inline_dict(d: dict) -> str:
    """Serialize a flat dict (scalar values only) as a TOML inline table.

    Drives the handle (gh-306) nested ``methods.args`` / ``getters.fields``
    arrays — each member is one ``{ name = "x", type = "double", … }`` table.
    Booleans render bare, everything else as a quoted string (the manifest keeps
    numeric defaults as strings, matching the rest of ``_dump``)."""
    parts = []
    for k, v in d.items():
        if isinstance(v, bool):
            parts.append(f"{k} = {'true' if v else 'false'}")
        else:
            parts.append(f'{k} = "{v}"')
    return "{ " + ", ".join(parts) + " }"


def _dump_handle_subtables(mk: str, data: dict) -> list[str]:
    """Render a handle module's create_args / create_post / methods / getters
    sub-tables (gh-306). Each is a ``[[module.X.<tbl>]]`` array; nested
    ``methods.args`` and ``getters.fields`` are inline-table arrays, so the whole
    spec round-trips through ``load`` / ``save`` unchanged."""
    out: list[str] = []

    for a in data.get("create_args", []):
        out.append(f"[[module.{mk}.create_args]]")
        out.append(f'name = "{a["name"]}"')
        out.append(f'type = "{a["type"]}"')
        if a.get("enum"):
            out.append(f'enum = "{a["enum"]}"')
        if a.get("default") not in (None, ""):
            out.append(f'default = "{a["default"]}"')
        if a.get("kwonly"):
            out.append("kwonly = true")
        out.append("")

    for p in data.get("create_post", []):
        out.append(f"[[module.{mk}.create_post]]")
        out.append(f'fn = "{p["fn"]}"')
        if p.get("when"):
            out.append(f'when = "{p["when"]}"')
        if "arg" in p:
            out.append(f'arg = "{p["arg"]}"')
        out.append("")

    for m in data.get("methods", []):
        out.append(f"[[module.{mk}.methods]]")
        out.append(f'name = "{m["name"]}"')
        out.append(f'fn = "{m["fn"]}"')
        if m.get("returns"):
            out.append(f'returns = "{m["returns"]}"')
        if m.get("nogil"):
            out.append("nogil = true")
        if m.get("args"):
            out.append(
                "args = ["
                + ", ".join(_inline_dict(a) for a in m["args"])
                + "]"
            )
        out.append("")

    for g in data.get("getters", []):
        out.append(f"[[module.{mk}.getters]]")
        # fn/out are absent for a per-field-getter table (gh-314); each field
        # then carries its own `getter` (dumped inline via _inline_dict).
        if g.get("fn"):
            out.append(f'fn = "{g["fn"]}"')
        if g.get("out"):
            out.append(f'out = "{g["out"]}"')
        if g.get("cache"):
            out.append("cache = true")
        if g.get("fields"):
            out.append(
                "fields = ["
                + ", ".join(_inline_dict(f) for f in g["fields"])
                + "]"
            )
        out.append("")

    return out


def _inline_computed(c: dict) -> str:
    """Serialize a computed read-only property (``{name, type, fn, doc?}``) as a
    TOML inline table — the source's derived-property declarations (gh-287)."""
    parts = [
        f'name = "{c["name"]}"',
        f'type = "{c["type"]}"',
        f'fn = "{c["fn"]}"',
    ]
    if c.get("doc"):
        parts.append(f'doc = "{c["doc"]}"')
    return "{ " + ", ".join(parts) + " }"


def _dump_composer_subtables(mk: str, data: dict) -> list[str]:
    """Render a composer module's source/segment/timeline/oo/json sub-tables
    (gh-287). Each is a single TOML table; field lists are inline-table arrays
    so the whole spec round-trips through ``load``/``save`` unchanged."""
    out: list[str] = []

    src = data.get("source")
    if src:
        out.append(f"[module.{mk}.source]")
        for k in ("object", "struct", "type_name"):
            if src.get(k):
                out.append(f'{k} = "{src[k]}"')
        fields = src.get("fields") or []
        if fields:
            out.append(
                "fields = ["
                + ", ".join(_inline_field(f) for f in fields)
                + "]"
            )
        computed = src.get("computed") or []
        if computed:
            out.append(
                "computed = ["
                + ", ".join(_inline_computed(c) for c in computed)
                + "]"
            )
        out.append("")
        gen = src.get("generates")
        if gen:
            out.append(f"[module.{mk}.source.generates]")
            for k in (
                "generator",
                "bridge_fn",
                "state_type",
                "steps_fn",
                "step_fn",
                "reset_fn",
                "destroy_fn",
                "header",
                "output_type",
            ):
                if gen.get(k):
                    out.append(f'{k} = "{gen[k]}"')
            out.append("")

    seg = data.get("segment")
    if seg:
        out.append(f"[module.{mk}.segment]")
        for k in (
            "type_name",
            "struct",
            "sources",
            "sources_member",
            "count_member",
        ):
            if seg.get(k):
                out.append(f'{k} = "{seg[k]}"')
        if seg.get("flat_sources"):
            out.append("flat_sources = true")
        fields = seg.get("fields") or []
        if fields:
            out.append(
                "fields = ["
                + ", ".join(_inline_field(f) for f in fields)
                + "]"
            )
        out.append("")

    tl = data.get("timeline")
    if tl:
        out.append(f"[module.{mk}.timeline]")
        if tl.get("type_name"):
            out.append(f'type_name = "{tl["type_name"]}"')
        if tl.get("loop"):
            out.append(
                "loop = [" + ", ".join(f'"{x}"' for x in tl["loop"]) + "]"
            )
        out.append("")

    oo = data.get("oo")
    if oo:
        out.append(f"[module.{mk}.oo]")
        if oo.get("factories"):
            out.append(
                "factories = ["
                + ", ".join(f'"{x}"' for x in oo["factories"])
                + "]"
            )
        for k in ("emit", "discriminant", "composer_type_name"):
            if oo.get(k):
                out.append(f'{k} = "{oo[k]}"')
        out.append("")

    comp = data.get("composer")
    if comp:
        out.append(f"[module.{mk}.composer]")
        if comp.get("stream"):
            out.append("stream = true")
        if comp.get("to_dict"):
            out.append("to_dict = true")
        rt = comp.get("realtime")
        if rt:
            # gh-317: the realtime stream clock fns as an inline table.
            _rt = ", ".join(f'{k} = "{v}"' for k, v in rt.items())
            out.append(f"realtime = {{ {_rt} }}")
        out.append("")

    js = data.get("json")
    if js:
        out.append(f"[module.{mk}.json]")
        out.append("enabled = " + ("true" if js.get("enabled") else "false"))
        for k in ("to_json_fn", "from_json_fn", "from_file_fn"):
            if js.get(k):
                out.append(f'{k} = "{js[k]}"')
        if js.get("to_json_trailing"):
            out.append(
                "to_json_trailing = ["
                + ", ".join(f'"{x}"' for x in js["to_json_trailing"])
                + "]"
            )
        out.append("")

    # gh-317: delegated serializers (to_sigmf, …) — each a [[X.serializers]]
    # table with an inline-table `params` array.
    for s in data.get("serializers", []):
        out.append(f"[[module.{mk}.serializers]]")
        out.append(f'name = "{s["name"]}"')
        out.append(f'fn = "{s["fn"]}"')
        if s.get("returns"):
            out.append(f'returns = "{s["returns"]}"')
        if s.get("header"):  # gh-343: #include for the serializer fn's decl
            out.append(f'header = "{s["header"]}"')
        if s.get("params"):
            out.append(
                "params = ["
                + ", ".join(_inline_field(p) for p in s["params"])
                + "]"
            )
        out.append("")

    return out


def _property_dump_lines(p: dict, header: str) -> list[str]:
    """TOML lines for one property under *header* (ends with a blank line).

    Shared (gh-504) by an object's ``[[<comp>.properties]]`` and a view's
    ``[[<comp>.views.properties]]`` so the two emit identically.
    """
    lines = [header, f'name = "{p["name"]}"']
    if p.get("doc"):
        lines.append(_doc_assign(p["doc"]))
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
    return lines


def _method_dump_lines(m: dict, header: str) -> list[str]:
    """TOML lines for one method under *header* (ends with a blank line).

    Shared (gh-504) by an object's ``[[<comp>.methods]]`` and a view's
    ``[[<comp>.views.methods]]``.
    """
    lines = [header, f'name = "{m["name"]}"']
    if m.get("doc"):
        lines.append(_doc_assign(m["doc"]))
    if m.get("arg_type"):
        lines.append(f'arg_type = "{m["arg_type"]}"')
    if m.get("return_type"):
        lines.append(f'return_type = "{m["return_type"]}"')
    if m.get("varargs"):
        lines.append("varargs = true")
    if m.get("manual_stub"):
        lines.append("manual_stub = true")
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

        def _param_inline(p: dict) -> str:
            s = f'name = "{p["name"]}", type = "{p["type"]}"'
            # gh-240: an optional scalar default round-trips as a string.
            if p.get("default") not in (None, ""):
                s += f', default = "{p["default"]}"'
            # gh-432: capsule-typed params — the capsule name and the
            # foreign type's header must survive save()/load(); the
            # gh-257 generic passthrough covers only method-level
            # scalar keys, not per-param keys.
            if p.get("capsule"):
                s += f', capsule = "{p["capsule"]}"'
            if p.get("header"):
                s += f', header = "{p["header"]}"'
            return "{" + s + "}"

        parts = ", ".join(_param_inline(p) for p in _ea)
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
    if m.get("single"):
        lines.append("single = true")
    if m.get("py_return_type"):
        lines.append(f'py_return_type = "{m["py_return_type"]}"')
    if m.get("max_out"):
        lines.append(f"max_out = {m['max_out']}")
    # gh-257: preserve any manifest-authored scalar key the explicit block
    # above doesn't know (e.g. `record_name`). List/table keys are already
    # emitted above; `_`-prefixed keys are transient (e.g. `_doc_blocks`).
    # Zero churn — jm only writes known keys, so this emits nothing for
    # jm-generated manifests.
    for _k, _v in m.items():
        if (
            _k in _KNOWN_METHOD_KEYS
            or _k.startswith("_")
            or isinstance(_v, (list, dict))
        ):
            continue
        if isinstance(_v, bool):
            lines.append(f"{_k} = {'true' if _v else 'false'}")
        elif isinstance(_v, (int, float)):
            lines.append(f"{_k} = {_v}")
        else:
            lines.append(f'{_k} = "{_v}"')
    lines.append("")
    return lines


def _dump(cfg: dict) -> str:
    lines: list[str] = []

    proj = cfg.get("project", {})
    if proj:
        lines.append("[project]")
        # Nested sub-tables (e.g. `bench`) must follow the scalar keys in TOML,
        # so collect them and emit `[project.<name>]` blocks after this loop.
        subtables = {}
        for k, v in proj.items():
            if isinstance(v, dict):
                subtables[k] = v
            elif k in ("c_deps", "find_packages", "pkg_modules", "platforms"):
                items_str = ", ".join(f'"{x}"' for x in v)
                lines.append(f"{k} = [{items_str}]")
            else:
                lines.append(f'{k} = "{v}"')
        for name, sub in subtables.items():
            lines.append("")
            lines.append(f"[project.{name}]")
            for k, v in sub.items():
                if isinstance(v, (list, tuple)):
                    items_str = ", ".join(
                        str(x) if isinstance(x, (int, float)) else f'"{x}"'
                        for x in v
                    )
                    lines.append(f"{k} = [{items_str}]")
                elif isinstance(v, bool):
                    lines.append(f"{k} = {str(v).lower()}")
                elif isinstance(v, (int, float)):
                    lines.append(f"{k} = {v}")
                else:
                    lines.append(f'{k} = "{v}"')
        lines.append("")

    # [[enum]] SSOT tables (top-level, manifest-owned) — render before modules.
    for e in cfg.get("enum", []):
        lines.append("[[enum]]")
        lines.append(f'name = "{e["name"]}"')
        vals_str = ", ".join(f'"{v}"' for v in e.get("values", []))
        lines.append(f"values = [{vals_str}]")
        lines.append("")

    for mod, data in cfg.get("module", {}).items():
        lines.append(f"[module.{_module_key(mod)}]")
        if data.get("no_generate") in (True, "true"):
            lines.append('no_generate = "true"')
        if data.get("kind") in ("capsule", "composer", "handle"):
            # gh-286/gh-287/gh-306: capsule + composer + handle modules expose
            # an opaque backing (composer adds OO types; handle a typed class) —
            # no `objects` list.
            lines.append(f'kind = "{data["kind"]}"')
            if data.get("backing"):
                lines.append(f'backing = "{data["backing"]}"')
            if data.get("capsule_name"):
                lines.append(f'capsule_name = "{data["capsule_name"]}"')
            if data.get("package"):
                lines.append(f'package = "{data["package"]}"')
            if data.get("header"):
                lines.append(f'header = "{data["header"]}"')
            if data.get("kind") == "handle":
                # gh-306: the typed-class scalar keys.
                for _hk in (
                    "type_name",
                    "create_fn",
                    "init_fn",
                    "close_fn",
                    "handle_type",
                    "optional_backend",
                    "serializable",  # gh-403: state triplet over the handle
                ):
                    if data.get(_hk):
                        lines.append(f'{_hk} = "{data[_hk]}"')
                if data.get("context_manager"):
                    lines.append("context_manager = true")
            if data.get("kind") == "composer":
                # gh-287: composes a generator source object; sample_type turns
                # on the inherited jm-app output axes.
                if data.get("composes"):
                    cstr = ", ".join(f'"{c}"' for c in data["composes"])
                    lines.append(f"composes = [{cstr}]")
                if data.get("sample_type"):
                    lines.append("sample_type = true")
            if data.get("depends_on"):
                parts = []
                for d in data["depends_on"]:
                    if isinstance(d, dict):
                        inner = f'name = "{d["name"]}"'
                        if d.get("link"):
                            inner += ", link = true"
                        parts.append(f"{{ {inner} }}")
                    else:
                        parts.append(f'"{d}"')
                lines.append(f"depends_on = [{', '.join(parts)}]")
        elif data.get("functions_in_core") in (True, "true"):
            lines.append('functions_in_core = "true"')
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
            if fn.get("variable_output"):
                lines.append("variable_output = true")
            if fn.get("out_size"):
                lines.append(f'out_size = "{fn["out_size"]}"')
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
                    # gh-240: an optional scalar default round-trips as a string.
                    if p.get("default") not in (None, ""):
                        base += f', default = "{p["default"]}"'
                    # gh-432: capsule-typed params round-trip their capsule
                    # name and foreign header.
                    if p.get("capsule"):
                        base += f', capsule = "{p["capsule"]}"'
                    if p.get("header"):
                        base += f', header = "{p["header"]}"'
                    _emit.append("{" + base + "}")
                lines.append(f"params = [{', '.join(_emit)}]")
            if fn.get("inline"):
                lines.append("inline = true")
            lines.append("")

        # gh-286/gh-287: capsule + composer sub-tables — create params,
        # methods, props (shared); composer adds source/segment/timeline/oo/json.
        if data.get("kind") in ("capsule", "composer"):
            mk = _module_key(mod)
            for p in data.get("init_params", []):
                lines.append(f"[[module.{mk}.init_params]]")
                lines.append(f'name = "{p["name"]}"')
                lines.append(f'type = "{p["type"]}"')
                if p.get("default") not in (None, ""):
                    lines.append(f'default = "{p["default"]}"')
                lines.append("")
            for m in data.get("methods", []):
                lines.append(f"[[module.{mk}.methods]]")
                lines.append(f'name = "{m["name"]}"')
                if m.get("arg_type"):
                    lines.append(f'arg_type = "{m["arg_type"]}"')
                if m.get("return_type"):
                    lines.append(f'return_type = "{m["return_type"]}"')
                if m.get("caller_out"):
                    lines.append("caller_out = true")
                if m.get("nogil"):
                    lines.append("nogil = true")
                lines.append("")
            for pr in data.get("properties", []):
                lines.append(f"[[module.{mk}.properties]]")
                lines.append(f'name = "{pr["name"]}"')
                lines.append(f'type = "{pr["type"]}"')
                if pr.get("writable"):
                    lines.append("writable = true")
                lines.append("")
        if data.get("kind") == "composer":
            lines += _dump_composer_subtables(_module_key(mod), data)
        if data.get("kind") == "handle":
            lines += _dump_handle_subtables(_module_key(mod), data)

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
            "serializable",
            "streamable",
            "async_stream",
            "stream_block_default",
            "class_name",
            # gh-509: object-level C constructor override. A plain object whose
            # backing create is not the default ``<comp>_create`` (e.g. acq's
            # ``acq_create_continuous``) declares it here so the generated
            # tp_init calls it — no hand-patch that regeneration would drop.
            "create_fn",
            # gh-482. `create_error` is a name from ERROR_CATEGORIES, so the
            # raw f-string emission below is safe for it. Its paired
            # `create_error_message` is human prose and is emitted separately
            # via _str_assign — this loop does no escaping, so a message
            # containing a quote would produce broken TOML here.
            "create_error",
        )
        lines.append(f"[{comp}]")
        for k in scalar_keys:
            if k in comp_data:
                lines.append(f'{k} = "{comp_data[k]}"')
        if comp_data.get("create_error_message"):
            lines.append(
                _str_assign(
                    "create_error_message", comp_data["create_error_message"]
                )
            )
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
            if s.get("controllable"):
                lines.append("controllable = true")
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
            if p.get("required"):
                lines.append("required = true")
            if p.get("doc"):
                lines.append(_doc_assign(p["doc"]))
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
            lines += _method_dump_lines(m, f"[[{comp}.methods]]")
        for p in comp_data.get("properties", []):
            lines += _property_dump_lines(p, f"[[{comp}.properties]]")
        # gh-481. An explicit closed field list (the `properties` style, not
        # the `methods` generic passthrough): the grammar is deliberately small
        # and fixed, so a key that isn't one of these is an authoring error
        # worth losing loudly at validation rather than round-tripping
        # silently. gh-482 adds a sibling [[<comp>.errors]] table here.
        for w in comp_data.get("warnings", []):
            lines.append(f"[[{comp}.warnings]]")
            lines.append(f'after = "{w.get("after", "__init__")}"')
            lines.append(f'condition = "{w["condition"]}"')
            lines.append(f'category = "{w.get("category", "UserWarning")}"')
            lines.append(_str_assign("message", w["message"]))
            if w.get("stacklevel"):
                lines.append(f"stacklevel = {int(w['stacklevel'])}")
            lines.append("")
        # gh-504. A view is a second class over the same core. Its own
        # init_params serialize inline (a nested [[...]] table under a [[...]]
        # table is awkward TOML); exclude_properties is a plain list key like
        # multi_output. Nothing emitted when the list is empty → zero churn.
        for v in comp_data.get("views", []):
            lines.append(f"[[{comp}.views]]")
            lines.append(f'class_name = "{v["class_name"]}"')
            lines.append(f'create_fn = "{v["create_fn"]}"')
            if v.get("doc"):
                lines.append(_doc_assign(v["doc"]))
            if v.get("init_params"):

                def _ip_inline(p: dict) -> str:
                    s = f'name = "{p["name"]}", type = "{p["type"]}"'
                    if p.get("default") not in (None, ""):
                        s += f', default = "{p["default"]}"'
                    if p.get("optional"):
                        s += ", optional = true"
                    if p.get("create_fn"):
                        s += f', create_fn = "{p["create_fn"]}"'
                    if p.get("required"):
                        s += ", required = true"
                    return "{" + s + "}"

                parts = ", ".join(_ip_inline(p) for p in v["init_params"])
                lines.append(f"init_params = [{parts}]")
            if v.get("exclude_properties"):
                ep = ", ".join(f'"{n}"' for n in v["exclude_properties"])
                lines.append(f"exclude_properties = [{ep}]")
            if v.get("exclude_methods"):
                em = ", ".join(f'"{n}"' for n in v["exclude_methods"])
                lines.append(f"exclude_methods = [{em}]")
            # gh-504/gh-509: a view's own added/overriding members and its own
            # warnings nest under it. All the view's scalar keys above must
            # precede these subtables (TOML binds [[<comp>.views.properties]]
            # to the preceding [[…views]]).
            v_methods = v.get("methods", [])
            v_props = v.get("properties", [])
            v_warnings = v.get("warnings", [])
            for w in v_warnings:
                lines.append(f"[[{comp}.views.warnings]]")
                lines.append(f'after = "{w.get("after", "__init__")}"')
                lines.append(f'condition = "{w["condition"]}"')
                lines.append(
                    f'category = "{w.get("category", "UserWarning")}"'
                )
                lines.append(_str_assign("message", w["message"]))
                if w.get("stacklevel"):
                    lines.append(f"stacklevel = {int(w['stacklevel'])}")
                lines.append("")
            for m in v_methods:
                lines += _method_dump_lines(m, f"[[{comp}.views.methods]]")
            for p in v_props:
                lines += _property_dump_lines(
                    p, f"[[{comp}.views.properties]]"
                )
            # The nested helpers each emit a trailing blank; only add the
            # view separator when there were none.
            if not v_methods and not v_props and not v_warnings:
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

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

import copy as _copy
import json as _json
import re as _re
import sys as _sys

# gh-838: the ordered init-param field list lives in `_keys`, beside the
# validator that already knew those key names. Two lists — one saying which
# keys are legal and one saying which get written — is precisely how
# `capsule` and `header` came to be accepted by the first and dropped by the
# second. `_keys` imports only `_report`, so this is not a cycle.
from ._keys import INIT_PARAM_FIELDS as _INIT_PARAM_FIELDS

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, NamedTuple

from . import _types as _T

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
    # gh-764: inside a `deferred_save()` scope the pending config has not
    # reached disk, so serve it from there — otherwise a replay step would read
    # back the state before its predecessor's write and silently lose it.
    if _DEFERRED is not None:
        pending = _DEFERRED.get(_deferral_key(root))
        if pending is not None:
            return _copy.deepcopy(pending)
    cfg = load_manifest(root)
    includes = cfg.pop("include", None)
    if includes:
        for fragment_path in _resolve_includes(root, includes):
            with fragment_path.open("rb") as f:
                fragment = tomllib.load(f)
            _merge_fragment(cfg, fragment, fragment_path)
    # gh-816: an unknown key is still accepted and still round-trips (gh-257);
    # it just no longer does so in silence. Here rather than in each command
    # because this is the one place every reader passes through, and a
    # wrong-kind key is worth reporting whichever command surfaced it. The
    # walk is deduplicated per process, so `apply` loading both the real tree
    # and its temp scaffold says each thing once.
    from ._keys import warn_unknown_keys

    warn_unknown_keys(cfg)
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


_SCRATCH_WRITES = False


@contextmanager
def scratch_writes() -> "Iterator[None]":
    """Write manifests with the plain dumper for the duration (gh-698).

    ``_write_doc`` round-trips an existing file through tomlkit so a user's
    comments, key order and array layout survive a mutating command. That is
    the right default and gh-491 exists because it once wasn't — but it is
    ``O(file_size)`` on every save, and ``apply``'s replay calls one mutating
    command **per method**, each of which reloads and rewrites the *whole*
    manifest. The product is quadratic: doppler's 67 KB manifest never
    finished, and a synthetic 48-method project spends a third of its apply
    inside tomlkit.

    Inside a replay that work buys nothing. The tree being written is a
    throwaway scratch root that the replay itself just synthesized from
    ``cfg``, so every "comment" tomlkit is carefully preserving was emitted by
    ``_dump()`` moments earlier. ``_apply`` never copies the scratch manifest
    back either — it is in ``_SKIP_FILES`` — so the formatting difference
    cannot reach the user's tree.

    Scoped as a context manager rather than a parameter because ``save`` is
    called from deep inside each command module; the flag's honest scope is
    "we are materializing a scratch tree", which is exactly one call site.
    """
    global _SCRATCH_WRITES
    prev = _SCRATCH_WRITES
    _SCRATCH_WRITES = True
    try:
        yield
    finally:
        _SCRATCH_WRITES = prev


# gh-764: root -> the config `save()` would have written, when deferring.
# None means "write through", which is every path except a replay.
_DEFERRED: "dict[Path, dict] | None" = None


def _deferral_key(root: Path) -> Path:
    """Normalise *root* so a relative and an absolute path share a cache slot."""
    return Path(root).resolve()


@contextmanager
def deferred_save() -> "Iterator[None]":
    """Coalesce every manifest write in this scope into one flush (gh-764).

    The third member of the family `scratch_writes` and
    ``_object.deferred_module_regen`` started (gh-698), and the same shape:
    ``apply``'s replay runs one mutating command per object, method, property
    and function, and each one ends by writing the whole manifest.

    gh-698 removed most of that cost — but only behind a guard. `scratch_writes`
    swaps the tomlkit round-trip for the plain `_dump`, *if* `_round_trips`
    confirms `_dump` reproduced the config. `_dump` is hand-written per section
    kind and is not total: it drops ``[codec.X]`` outright and renders a list
    value as its Python repr. A project with either — doppler has both — fails
    the guard on **every** save and pays the full tomlkit path regardless. 718
    saves, 2.48 million tomlkit ``__setitem__`` calls, 87% of a 90-second apply.

    Deferring sidesteps that entirely: with one write instead of 718, even the
    expensive path costs ~0.13 s, and `_dump`'s totality stops being a
    performance question. (It remains a correctness one — see gh-763.)

    Safe here because the tree being written is the **throwaway scaffold** the
    replay just synthesized from ``cfg``:

    - nothing outside the replay reads it, and the two readers inside it
      (`_apply._replay`'s ``C.load(temp_root)`` calls) are served from the
      cache, so they observe exactly what a write-through would have given;
    - the scratch manifest is in ``_apply._SKIP_FILES``, so it is never copied
      back and never part of what ``jm status`` byte-compares. Its intermediate
      states — and its final formatting — are unobservable.

    Only the end state has to be right, and one flush produces it.

    Configs are deep-copied in and out so `load` keeps returning a dict the
    caller owns, exactly as parsing from disk does. That costs ~2.9 ms on
    doppler against the ~127 ms it replaces, and it means a caller that loads,
    mutates and then decides *not* to save cannot corrupt the pending write.
    """
    global _DEFERRED
    prev = _DEFERRED
    _DEFERRED = {}
    try:
        yield
    finally:
        pending, _DEFERRED = _DEFERRED, prev
        # Restored first, so these are real writes (or, when nested, fold into
        # the enclosing deferral rather than escaping it).
        for pending_root, pending_cfg in pending.items():
            save(pending_root, pending_cfg)


def _round_trips(text: str, cfg: dict, include_list: list[str] | None) -> bool:
    """True when *text* parses back to exactly the config it was dumped from.

    The guard for the ``scratch_writes`` fast path (gh-698). Comparing the
    reparsed document against *cfg* catches any section kind ``_dump`` does not
    know how to render — the way it silently dropped ``[codec.X]`` — instead of
    trusting a hand-written serializer to be total.
    """
    try:
        got = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return False
    want = dict(cfg)
    if include_list:
        want["include"] = list(include_list)
    return got == want


def _matches_on_disk(
    path: Path, cfg: dict, include_list: list[str] | None
) -> bool:
    """True when *path* already parses to exactly *cfg* (plus *include_list*).

    gh-764. Shares `_round_trips`'s comparison rather than restating it — the
    question is identical ("does this text mean exactly this config?"), only
    the source of the text differs.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return _round_trips(text, cfg, include_list)


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

    # gh-764: the file may already say exactly this. `save` rewrites *every*
    # destination the project has — 70 files on doppler for a one-property
    # change — and 69 of them come back byte-identical after a tomlkit
    # round-trip costing ~11 ms each. tomllib answers "is it already right?"
    # in ~0.8 ms, so asking is ~14x cheaper than answering by rewriting.
    #
    # Skipping is not a trade against gh-491's layout preservation, it is the
    # same goal reached sooner: `_sync` deliberately leaves unchanged keys
    # alone so an author's formatting survives, and a file that already parses
    # to *cfg* has no changed keys to apply.
    if _matches_on_disk(path, cfg, include_list):
        return

    if _SCRATCH_WRITES:
        # gh-698: `_dump` is ~5x cheaper than building and dumping a tomlkit
        # document, but it is hand-written per section kind and is NOT known to
        # be total — it silently omits `[codec.X]`, and nothing guarantees that
        # is the only gap. So use it only when it demonstrably round-trips.
        #
        # The check is cheap (tomllib is the C parser) and turns "is `_dump`
        # faithful for this cfg?" from an assumption into a fact, per write.
        # When it is not, we fall through to the tomlkit path below and are
        # merely as slow as before rather than silently lossy.
        text = _dump(cfg)
        if include_list:
            text = f"include = {_toml_string_array(include_list)}\n\n" + text
        if _round_trips(text, cfg, include_list):
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

    # gh-698: parsing the existing file is the expensive half of a save, and
    # under `scratch_writes` there is nothing in it worth preserving. Syncing
    # into an EMPTY document runs the identical section logic below — which is
    # generic over every top-level key — so the result is faithful; it simply
    # carries no comments, which a scratch tree never had.
    #
    # Note this is *not* the same as taking the `_dump` fast path above: that
    # dumper is hand-written per section kind and silently omits `[codec.X]`,
    # so using it here dropped codecs from the replayed manifest and every
    # later step lost them.
    doc = (
        _tk.document()
        if _SCRATCH_WRITES
        else _tk.loads(path.read_text(encoding="utf-8"))
    )

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
    # gh-764: under `deferred_save()` this becomes a cache update; the single
    # real write happens when that scope exits.
    #
    # Deferred only once the manifest exists. A dozen commands gate on
    # `(root / FILENAME).exists()` rather than on a load, so the scaffold's
    # very first save has to reach disk or every replay step after it exits
    # with "no just-makeit.toml found". That write is the bootstrap one; the
    # hundreds that follow are what this exists to collapse.
    if _DEFERRED is not None and (root / FILENAME).exists():
        _DEFERRED[_deferral_key(root)] = _copy.deepcopy(cfg)
        return
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
        if key in ("project", "module", "include", "app", "enum", "codec"):
            continue  # `app`/`enum`/`codec`, like `project`, live in the manifest
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
    if cfg.get("codec"):
        manifest_content["codec"] = cfg[
            "codec"
        ]  # [codec.X] SSOT, manifest-owned
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
    return [
        k
        for k in cfg
        if k not in ("project", "module", "app", "enum", "codec")
    ]


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


def valid_identifier(name: str) -> bool:
    """True when *name* can be written into generated C and Python unchanged.

    gh-625: this predicate was written out five separate times — for the
    project, component, object, function, and module-segment names — and
    reachable from none of the commands that were missing it. `jm property`
    and `jm method` accepted any string, wrote it into the **sacred** header,
    the binding, the stub and the manifest, and exited 0; the next `make`
    then failed in generated code the user did not write, and the stub was
    not parseable Python.

    One implementation, so a command added later inherits the check instead
    of being remembered. Semantics are byte-for-byte what the five copies
    did, deliberately: they accept more than the message describes (`Foo`
    and `café` both pass), and tightening that would reject names existing
    projects may already carry. That mismatch is worth its own issue, not a
    silent change here.
    """
    return (
        bool(name)
        and name.replace("_", "").isalnum()
        and not name[0].isdigit()
    )


def validate_name(name: str, kind: str) -> str | None:
    """Return the standard error message for an invalid *kind* name, or
    ``None``. *kind* is the noun the user sees: ``"object"``, ``"property"``,
    ``"method"``, ``"function"``, ``"project"``, ``"component"``."""
    if valid_identifier(name):
        return None
    return (
        f"'{name}' is not a valid {kind} name.\n"
        "Use lowercase letters, digits, and underscores only; "
        "must not start with a digit."
    )


def require_name(name: str, kind: str) -> None:
    """Exit 1 with the standard message when *name* is not a valid
    identifier. The one-line form for a command's argument check."""
    msg = validate_name(name, kind)
    if msg:
        print(f"error: {msg}", file=_sys.stderr)
        _sys.exit(1)


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
        if not valid_identifier(seg):
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


def is_opaque_state(cfg: dict, component: str) -> bool:
    """True if the state struct is forward-declared in the header (gh-588).

    An object's ``<comp>_state_t`` is otherwise always a *complete* type in the
    public header, so adopting the object kind for a resource-ish component
    means exporting every member as API — a `FILE *`, a scratch buffer, a
    decoded-keyword array. ``opaque_state`` emits
    ``typedef struct <comp>_state <comp>_state_t;`` instead and leaves the
    definition to hand-written ``_core.c``, which is what the handle kind gives
    you for nothing (#525 finding 8).

    Not to be confused with `opaque_fields`, which declares individual members
    of a *published* struct that jm does not manage.
    """
    return _truthy(cfg.get(component, {}).get("opaque_state"))


def is_no_reset(cfg: dict, component: str) -> bool:
    """Return True if the component was scaffolded with --no-reset.

    gh-542: some objects have nothing coherent to reset — a writer whose
    samples are already on disk and whose written-sample count drives the
    header patch applied at close would be *corrupted* by a reset, not
    returned to a clean state. The honest answer there is "construct a new
    one", and the only way to say that without degrading silently (a C no-op
    returns None, so the caller believes it worked) is to not ship the method
    at all. Symmetric with `is_no_step` / `is_no_state`.
    """
    return _truthy(cfg.get(component, {}).get("no_reset"))


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


def module_doc(cfg: dict, module: str) -> str:
    """Return the module's declared documentation, or ``""``.

    Canonical reader for ``[module.X] doc`` (gh-645). A module is the one
    surface with no header to derive from -- its ``m_doc`` and its re-export
    ``__init__.py`` are both wholly jm-generated -- so the manifest is the only
    place an author can say what the module is for. The same string feeds both
    faces: the extension's ``m_doc`` (what ``help(pkg.mod)`` prints) and a real
    module docstring on the shim (what griffe/mkdocstrings reads for the
    module's page).

    Examples
    --------
    >>> module_doc({"module": {"agc": {"doc": "Gain control."}}}, "agc")
    'Gain control.'
    >>> module_doc({"module": {"agc": {}}}, "agc")
    ''
    """
    return str(cfg.get("module", {}).get(module, {}).get("doc", "") or "")


def module_package(cfg: dict, module: str) -> str:
    """Return the package directory a module's Python artifacts land in.

    This is the canonical reader for ``[module.X] package`` and applies to
    *every* module kind — plain object groups (gh-523), capsules, composers and
    handles alike. A module frequently lives *inside* a sibling package rather
    than its own: doppler's ``ddc_fn`` capsule is built into the ``ddc``
    package so ``doppler.ddc`` can re-export its free functions, and its
    ``wfm_reader`` object module lands in the pre-existing ``wfm`` package
    beside ``Synth`` / ``Composer``. Declare it with::

        [module.wfm_reader]
        objects = ["wfm_reader"]
        package = "wfm"

    When unset the module's own ``pypath`` is used, so a standalone module
    (``<pkg>.<module>``) needs no key. Callers spell the fallback explicitly —
    ``C.module_package(cfg, m) or mp.pypath`` — because ``mp`` is already in
    scope wherever the path is being built.

    :func:`capsule_package` and :func:`handle_package` are kind-flavoured
    aliases that delegate here; there is exactly one implementation."""
    return cfg.get("module", {}).get(module, {}).get("package", "")


def capsule_package(cfg: dict, module: str) -> str:
    """Package directory a capsule module's ``.so`` / ``.pyi`` land in.

    Alias for :func:`module_package` — the key is not capsule-specific."""
    return module_package(cfg, module)


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


def handle_capsule(cfg: dict, module: str) -> str:
    """The ``PyCapsule`` name a handle module publishes, or ``""`` (gh-794).

    When set, the generated type gains a ``_capsule`` property lending its
    opaque ``<handle_type> *`` — borrowed and non-owning, exactly as an
    object's gh-788 gap-4 property does, so a handle drops straight into a
    gh-432 method param or a gh-790 constructor with no change on the
    consuming side.

    A handle is the shape most likely to be on the *giving* end of a capsule —
    it exists to wrap a long-lived resource another component wants to borrow —
    and before this it was the only kind that could not give one.

    Falls back to the module's ``capsule_name`` (which a ``kind = "capsule"``
    module already uses for the same purpose) so the two kinds spell one idea
    one way; empty means the property is not generated at all, leaving every
    existing handle module byte-identical.
    """
    m = cfg.get("module", {}).get(module, {})
    return str(m.get("capsule") or m.get("capsule_name") or "")


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


def handle_create_error(cfg: dict, module: str) -> str:
    """Exception class for a handle's ``create_fn`` failure (gh-514).

    The handle counterpart of :func:`create_error`. The two cannot share one
    accessor because a handle's keys live under ``[module.<name>]`` while an
    object's live in its own top-level table, so ``create_error(cfg, comp)``
    silently returned "" for every handle — the whole defect in gh-514.

    Empty means undeclared, which keeps the historical blanket
    ``RuntimeError: <create_fn> failed``.
    """
    return cfg.get("module", {}).get(module, {}).get("create_error", "")


def handle_create_error_message(cfg: dict, module: str) -> str:
    """Message paired with `handle_create_error` ("" if undeclared)."""
    return (
        cfg.get("module", {}).get(module, {}).get("create_error_message", "")
    )


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


def handle_factories(cfg: dict, module: str) -> list[dict]:
    """Return a handle's ``[[module.X.factories]]`` — module-level alternate
    constructors (gh-565).

    Each is ``{name, create_fn, init_params}``: a module-level function that
    parses its ``init_params`` (a ``bytes`` blob or a ``path``, or scalars),
    calls ``create_fn`` to build a FRESH handle, wraps it in the module's typed
    class, and returns it — e.g. ``PlanFromBlob(blob) -> Plan`` over
    ``wfm_plan_restore``. The write twin of a ``returns = "bytes"`` method; the
    two together give zero-binding save/restore. ``init_params`` reuse the same
    ``{name, type, default?}`` shape as ``create_args``."""
    return list(cfg.get("module", {}).get(module, {}).get("factories", []))


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
    module's own ``pypath`` when unset. See :func:`module_package`."""
    return module_package(cfg, module)


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
    Pairs with the ``--allow`` CLI flag (gh-140).

    **An entry exempts the FILE, not the finding that prompted it.** One
    matcher serves every check, so a pattern added to accept (say) a drifted
    constructor also accepts that file's ``stale``/``missing`` classification,
    and the next genuine divergence in it is masked. That breadth is
    deliberate — one mechanism is worth more than a key per check — but it is
    wider than the reasoning of the person adding the entry, who is usually
    thinking about one parameter. Prefer the narrowest path that covers the
    case, and expect the exemption to be re-examined rather than permanent:
    ``jm status`` lists allowed deviations precisely so a stale entry can be
    spotted and removed (gh-823).

    Never suppressed, whatever the pattern: a dropped ``.pyi`` symbol
    (gh-426) and a default-doc mismatch (gh-442), which are content loss
    rather than accepted difference."""
    return list(cfg.get("project", {}).get("status_allow", []))


def strict_examples(cfg: dict) -> bool:
    """True when an overlong authored ``@code`` line should fail the gate.

    gh-760. gh-752 gave `jm status --check` a burn-down *count* of authored
    example lines too wide for their generated stub, and printing a count is
    right while a project is sweeping. It is not enough afterwards: once the
    number reaches zero nothing stops the next line, so the sweep has to be
    re-run periodically forever.

    Off by default, so no existing consumer goes red mid-sweep, and
    declarative rather than a CI flag — like ``platforms`` and
    ``status_allow``, it travels with the repo rather than living in one
    invocation that a second workflow can forget.

    The enforcement can only live here. A formatter cannot do it: fed a
    doctest at a 79-column limit, clang-format reflows the block as prose,
    orphaning a comment onto its own line where doctest reads it as expected
    output and swallowing the real expected value onto a continuation. It
    reports success while destroying both examples — so this has to be a
    *checker*, never a fixer. And a downstream `.pyi` lint fires one
    transform too late, points at a generated file the author must not edit,
    and cannot state the budget, which is per-destination-indent and
    jm-internal.
    """
    return _truthy(cfg.get("project", {}).get("strict_examples", False))


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


def codecs(cfg: dict) -> dict:
    """Return the project's declared variant codecs, ``{name: {…}}`` (gh-554).

    The manifest-owned SSOT (like :func:`enums`) behind zero-hand-binding
    read/write of discriminant-tagged binary values; a method packs one with
    ``codec = "<name>"`` and a container property decodes one the same way. The
    per-codec model helpers live in :mod:`just_makeit._codec`.
    """
    return cfg.get("codec", {}) or {}


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


def _dep_test_only(d) -> bool:
    """Whether a depends_on entry exists only to link the component's C test.

    gh-537: ``depends_on`` is additive — a dependency lands on the component's
    core, its test and bench, *and* the shipped ``.so``. That is right for a
    real dependency, but a component whose C test round-trips through a sibling
    (doppler's reader writes the captures it then reads back) has to declare
    that sibling, which then ships inside the artifact. The cost is not the few
    KB: the manifest ends up asserting a dependency the shipped artifact does
    not have, and the manifest is meant to be the project's source of truth.

    ``{name = "wfm_writer", test_only = true}`` keeps the dependency on the test
    and bench link lines and off everything the artifact is built from.
    """
    return bool(d.get("test_only")) if isinstance(d, dict) else False


def depends_on(cfg: dict, component: str) -> list[str]:
    """Return the transitive C OBJECT library deps for a component.

    Each name in the list gets a target_sources line emitted *before* the
    component's own target_sources in the root CMakeLists, so the combined
    library target sees all required object files. An entry may be a bare
    string (``"fir"``) or a ``{name = "fir", link = true}`` table (gh-225);
    this returns the names either way (header includes + aggregate-lib
    objects apply to both forms).

    gh-537: ``test_only`` entries are excluded here. This accessor feeds what
    the shipped artifact is built from — header includes and the aggregate
    library's objects — and a test-only dependency belongs in neither. Callers
    that build the *test/bench* link line take :func:`depends_on_raw` (or
    :func:`depends_test_cores`) instead, which keep them.
    """
    return [
        _dep_name(d)
        for d in cfg.get(component, {}).get("depends_on", [])
        if not _dep_test_only(d)
    ]


def depends_test_only_cores(cfg: dict, component: str) -> list[str]:
    """``<name>_core`` targets declared ``test_only`` for a component (gh-537).

    These link the component's C test and bench and nothing else — not the
    core's PUBLIC link line (which would propagate them straight back into the
    Python extension), not the ``.so``, not the aggregate library.
    """
    out: list[str] = []
    for d in cfg.get(component, {}).get("depends_on", []):
        if not _dep_test_only(d):
            continue
        name = _dep_name(d)
        core = name if name.endswith("_core") else f"{name}_core"
        if core not in out:
            out.append(core)
    return out


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
        # gh-537: a test_only entry never reaches a shipped target, even if it
        # also carries link = true — the two would be contradictory, and
        # silently honouring `link` is how the dependency ends up in the .so.
        if _dep_links(d) and not _dep_test_only(d):
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
    """Return --init-param entries as 12-tuples.

    ``(name, type, default, default_raw, real_type, real_create_fn, optional,
    create_fn, required, doc, capsule, header)``

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
    a stub.  ``capsule`` / ``header`` (gh-790) make the param a foreign C
    pointer arriving as a named ``PyCapsule``: the object is constructed from
    a handle another module owns, and ``header`` is the include declaring that
    handle's type.  All fields default to ``""`` / ``False`` when absent.
    Callers may unpack defensively with ``param[:3]``.
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
            # gh-790: a capsule-typed init-param — the object is CONSTRUCTED
            # from a pointer another module published. `capsule` is the name
            # the capsule must carry; `header` is the include that declares
            # the pointed-to type, since it is not one jm knows.
            p.get("capsule", ""),
            p.get("header", ""),
        )
        for p in param_dicts
    ]


def init_param_tuple_to_dict(p: tuple) -> dict:
    """Convert one parsed init-param tuple to its stored-manifest dict shape.

    Shared by `add_component` and the `jm view` generator so both persist an
    identical ``init_params`` record (gh-504). Accepts the 3-to-12-field
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
    # gh-790. Dropping these would round-trip a capsule param back as a
    # scalar of an unknown C type, which is a KeyError at the next render —
    # the same class of silent key loss gh-432 hit on the method-param path.
    if len(p) > 10 and p[10]:
        rec["capsule"] = p[10]
    if len(p) > 11 and p[11]:
        rec["header"] = p[11]
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


# gh-595: method shapes for which `return_type` is not a Python-bound scalar,
# so an unregistered spelling is legitimate rather than a typo.
#
# - result_fields: names the user's record struct (`peaks_result_t`), used as
#   the `<struct> *result` buffer element or, with `single`, returned by value.
# - codec: a codec-pack method has no C core at all — `_method.run` treats its
#   arg_type/return_type as inert placeholders (gh-554).
# - manual_stub: the C binding is hand-written and jm declares nothing for it
#   (gh-428).
# - varargs: jm writes a sacred *args/**kwargs binding whose signature the
#   manifest's return_type does not describe.
_RETURN_TYPE_EXEMPT_KEYS = ("result_fields", "codec", "manual_stub", "varargs")


def _return_type_error(entry: dict, what: str, exempt_keys: tuple) -> str:
    """Validate one function/method entry's ``return_type``; "" when fine.

    Parameters
    ----------
    entry : dict
        A ``[[...functions]]`` or ``[[...methods]]`` table.
    what : str
        Human-readable location, e.g. ``"module 'ber' function
        'ber_lock_symbol'"``, used to open the message.
    exempt_keys : tuple of str
        Keys whose presence legitimises an unregistered ``return_type``.

    Returns
    -------
    str
        A multi-line error message, or ``""`` if the entry is valid.
    """
    rt = entry.get("return_type", "")
    # An absent return_type takes the generator default, which is registered.
    if not rt or any(entry.get(k) for k in exempt_keys):
        return ""
    if _T.is_supported_return_type(rt, allow_array=True):
        return ""
    help_text = _T.unsupported_return_type_help(rt)
    indented = "\n".join(f"  {line}" for line in help_text.splitlines())
    return f"{what}: unknown return_type '{rt}'.\n{indented}"


def _result_field_errors(entry: dict, what: str) -> list[str]:
    """Validate one entry's ``result_fields`` types (gh-598).

    Simpler than the ``return_type`` rule: a result field is always a scalar
    the binding has to convert into a Python object, so there is no shape that
    legitimises an unregistered type the way ``result_fields`` / ``out_type`` /
    ``codec`` do for a return type. Arrays are not a record field either.

    This is the backstop, not the primary fix — ``record_tuple_build`` now
    converts through ``_CTYPE_META``, so a registered type is *correct* rather
    than merely accepted. What remains is turning a genuine typo into a clear
    manifest error instead of a ``KeyError`` traceback from the renderer.

    Parameters
    ----------
    entry : dict
        A function/method table that may carry ``result_fields``.
    what : str
        Human-readable location, used to open each message.

    Returns
    -------
    list of str
        One message per offending field, in declaration order.
    """
    errors: list[str] = []
    for f in entry.get("result_fields", []) or []:
        ftype = f.get("type", "")
        if ftype in _T.SUPPORTED_TYPES:
            continue
        help_text = _T.unsupported_return_type_help(ftype, allow_void=False)
        indented = "\n".join(f"  {line}" for line in help_text.splitlines())
        errors.append(
            f"{what}: result field {f.get('name', '?')!r} has unknown "
            f"type '{ftype}'.\n{indented}"
        )
    return errors


def manifest_type_errors(cfg: dict) -> list[str]:
    """Every unusable type declared anywhere in the manifest.

    The manifest is the project's SSOT, but until gh-595 nothing checked the
    types it declared, and both tables that consumed them fell through
    silently rather than failing:

    - an unregistered ``return_type`` generated a binding that called the C
      function, discarded its result and returned ``None`` (gh-595);
    - an unmapped ``result_fields`` type reached ``Py_BuildValue`` under an
      ``int`` format with no cast, truncating wide values (gh-598).

    Both compiled cleanly. ``jm apply`` calls this and refuses to generate when
    it returns anything, which is the manifest-path counterpart of the check
    the ``jm function`` / ``jm method`` front-ends have always done.

    Covers module functions, component methods, view methods, and capsule /
    composer module methods — every table whose types reach a generated
    binding.

    Parameters
    ----------
    cfg : dict
        Parsed ``just-makeit.toml`` (including any merged fragments).

    Returns
    -------
    list of str
        One message per offending declaration, in manifest order. Empty when
        the manifest is clean.

    Examples
    --------
    >>> cfg = {"module": {"ber": {"functions": [
    ...     {"name": "lock", "return_type": "long"}]}}}
    >>> print(manifest_type_errors(cfg)[0].splitlines()[0])
    module 'ber' function 'lock': unknown return_type 'long'.
    >>> cfg = {"det": {"methods": [{"name": "scan", "return_type": "hit_t",
    ...     "result_fields": [{"name": "idx", "type": "wat_t"}]}]}}
    >>> print(manifest_type_errors(cfg)[0].splitlines()[0])
    'det' method 'scan': result field 'idx' has unknown type 'wat_t'.
    """
    errors: list[str] = []

    def _check(entry: dict, what: str, exempt: tuple) -> None:
        err = _return_type_error(entry, what, exempt)
        if err:
            errors.append(err)
        errors.extend(_result_field_errors(entry, what))

    for mod in modules(cfg):
        for fn in module_functions(cfg, mod):
            # A function's out_type forces the C return to void and makes
            # return_type inert (see _render.fn_c_decl), so it exempts too.
            _check(
                fn,
                f"module {mod!r} function {fn.get('name', '?')!r}",
                _RETURN_TYPE_EXEMPT_KEYS + ("out_type",),
            )
        for m in module_methods(cfg, mod):
            _check(
                m,
                f"module {mod!r} method {m.get('name', '?')!r}",
                _RETURN_TYPE_EXEMPT_KEYS,
            )
    for comp in components(cfg):
        for m in methods(cfg, comp):
            _check(
                m,
                f"{comp!r} method {m.get('name', '?')!r}",
                _RETURN_TYPE_EXEMPT_KEYS,
            )
        for v in views(cfg, comp):
            vname = v.get("class_name", "?")
            for m in view_methods(v):
                _check(
                    m,
                    f"{comp!r} view {vname!r} method {m.get('name', '?')!r}",
                    _RETURN_TYPE_EXEMPT_KEYS,
                )
    return errors


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

    gh-790: init-params are collected too, and FIRST. A capsule-typed
    init-param puts the foreign type in the ``<comp>_create()`` prototype —
    which sits in the sacred ``_core.h`` — so without its header the header
    does not even parse. Method params only need theirs in the binding, so
    they are the later, weaker case; both are the same key and the same list.
    """
    out: list[str] = []
    # Distinct names on purpose: an init-param is a TUPLE and a method param is
    # a DICT, so one loop variable for both reads as if they were the same
    # shape and type-checks as neither.
    for ip in init_params(cfg, component):
        h = ip[11] if len(ip) > 11 else ""
        if h and h not in out:
            out.append(h)
    for m in methods(cfg, component):
        for mp in m.get("params") or []:
            h = mp.get("header", "")
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


def view_create_error(cfg: dict, component: str, view: dict) -> str:
    """A view's create()-failure category, INHERITING the parent's (gh-580).

    Unlike `view_warnings` — where a view carries no parent warnings, so an
    undeclared view warns about nothing — a view and its parent construct the
    *same* object through different C constructors. The parent's translation is
    therefore almost always right for the view too, and inheriting it is what
    makes the common case correct with no extra declaration.

    That matters because a view exists precisely when the constructor takes
    different, usually *more*, parameters: ``RateConverter_create(rate,
    compensate)`` has two ways to be handed something invalid,
    ``RateConverter_create_matched(rate, compensate, pulse, beta, span,
    pulse_sps, num_phases)`` has seven. Before gh-580 the flavor was the only
    constructor that could not have a translation, so every failure on the
    class that needed it most surfaced as the blanket ``MemoryError`` that
    gh-482 exists to replace.

    Resolution is per key, with `view_create_error_message` as the sibling: a
    view declaring only a message refines the wording under the parent's
    category, and one declaring only a category reuses the parent's text. As at
    the object level, a message without a category is inert — `make_errors_ctx`
    renders the undeclared block whenever the category is empty.
    """
    if "create_error" in view:
        return view["create_error"]
    return create_error(cfg, component)


def view_create_error_message(cfg: dict, component: str, view: dict) -> str:
    """Message paired with `view_create_error`, inherited the same way."""
    if "create_error_message" in view:
        return view["create_error_message"]
    return create_error_message(cfg, component)


def set_view_create_error(
    cfg: dict, component: str, class_name: str, category: str, message: str
) -> dict:
    """Declare a view's OWN create()-failure translation (gh-580).

    Mirrors `set_create_error` at the object level and `add_view_warning`'s
    view targeting. Re-running replaces rather than accumulates: one failure
    channel, one translation.
    """
    view = _find_view(cfg, component, class_name)
    if view is None:
        raise KeyError(f"no view {class_name!r} on component {component!r}")
    view["create_error"] = category
    view["create_error_message"] = message
    return cfg


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

    **Legacy alias — ask :func:`c_formatting_on` instead.** Kept so an
    existing manifest keeps working, and still meaningful on its own: it is
    how a project says "format, with whatever ``clang-format`` is on PATH"
    without naming a command.
    """
    return str(cfg.get("project", {}).get("c_style", ""))


def c_formatting_on(cfg: dict) -> bool:
    """Whether jm formats the C it generates (gh-773).

    The single question every caller should ask. It used to be spelled
    ``c_style(cfg) == "clang-format"`` in five places, which is one
    reimplementation short of the rule against peer copies — and the reason
    the two keys drifted apart in meaning.

    **Declaring the command is the opt-in.** That is how the Python side has
    always worked (``_pyfmt``: "no-op unless ``py_format_command`` is
    declared"), and there is no ``py_style`` beside it because there is
    nothing for one to say. ``c_style`` has exactly one legal value, so it
    carried no information ``c_format_command`` does not — and the split's
    only product was a state where you set the command correctly and got
    **nothing**, with no warning, because the existing check only fires the
    other way round (doppler#616).

    So either key turns formatting on:

    - ``c_format_command = [...]`` — the modern spelling; names the binary,
      which is what makes the output reproducible across machines (gh-745).
    - ``c_style = "clang-format"`` — the original; means "use PATH's
      ``clang-format``", i.e. :data:`DEFAULT_C_FORMAT_COMMAND`.

    The behaviour change is confined to the combination that was broken:
    command declared, ``c_style`` unset now formats instead of silently doing
    nothing. A project in that state asked for formatting and was not getting
    it, and ``jm status`` shows the resulting diff on the next run.
    """
    if cfg.get("project", {}).get("c_format_command") is not None:
        return True
    return c_style(cfg) == "clang-format"


# The formatter invocation when none is declared. A bare name, resolved on
# PATH — which is what jm did unconditionally before gh-745.
DEFAULT_C_FORMAT_COMMAND = ["clang-format"]


def c_format_command(cfg: dict) -> list[str]:
    """The ``clang-format`` invocation, as an argv list (gh-745).

    ``c_style`` decides *whether* to format; this decides **which binary
    does it**, and that is the difference between a reproducible project and
    one whose committed bytes depend on the machine. jm used to resolve the
    formatter with a bare ``shutil.which("clang-format")``, so doppler got
    21.1.8 locally and 22.1.8 in CI — same input, different output, and
    ``jm status --check`` flips red across machines the moment ``c_style`` is
    on. A project that pins its formatter (via ``uv.lock``, a pre-commit
    mirror, or an absolute path) had no way to say so::

        [project]
        c_style = "clang-format"
        c_format_command = ["uvx", "clang-format==22.1.8"]

    The command must resolve the **same binary from any working directory**
    (gh-758) — jm formats its temp scaffold from outside the project, so
    ``["uv", "run", "--group", "dev", ...]``, which no-ops outside a project
    and falls back to ``PATH``, formats the two compared sides with two
    different formatters. ``jm status`` reports it when it happens.

    jm appends ``-i --style=file --fallback-style=LLVM`` and the file list, so
    the committed ``.clang-format`` still decides the layout — this changes
    only which executable reads it.

    An **argv list, never a shell string**: splitting a string would have to
    guess about quoting, and the first thing anyone puts here is a path that
    may contain spaces.

    Returns
    -------
    list of str
        The declared command, or ``["clang-format"]`` when unset — which
        reproduces jm's pre-gh-745 behaviour exactly.

    Raises
    ------
    ValueError
        If the value is not a non-empty list of strings. Failing loudly
        matters more than usual here: a silently-ignored formatter command
        looks identical to a working one until two machines disagree, which
        is the very failure this key exists to remove.
    """
    raw = cfg.get("project", {}).get("c_format_command")
    if raw is None:
        return list(DEFAULT_C_FORMAT_COMMAND)
    if isinstance(raw, str):
        raise ValueError(
            "[project] c_format_command must be a list of arguments, not a "
            f'string — write ["uv", "run", "clang-format"], not "{raw}". '
            "A string would have to be split, and jm will not guess where."
        )
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValueError(
            "[project] c_format_command must be a non-empty list of "
            f"arguments; got {raw!r}."
        )
    if not all(isinstance(a, str) for a in raw):
        raise ValueError(
            "[project] c_format_command must contain only strings; got "
            f"{raw!r}."
        )
    return [str(a) for a in raw]


def py_format_command(cfg: dict) -> list[str]:
    """The generated-Python formatter invocation, or ``[]`` when unset (gh-746).

    The Python twin of :func:`c_format_command`, with one deliberate shape
    difference: there is no separate ``py_style`` on/off key. jm has no
    house-style opinion to toggle for Python — it emits its own layout and
    the project either wants its own formatter run over the result or does
    not — so *declaring the command is the opt-in*::

        [project]
        py_format_command = ["uv", "run", "--group", "dev", "ruff", "format"]

    Routing through the project's own pinned invocation is the whole point,
    exactly as in gh-745: a bare ``ruff`` resolves to whatever is on PATH,
    and two ruff versions format the same input differently.

    Unset returns ``[]`` and nothing runs, so an existing project's output is
    byte-identical to before.

    Raises
    ------
    ValueError
        If the value is not a non-empty list of strings — the same contract
        as ``c_format_command``, for the same reason: a silently-ignored
        formatter command is indistinguishable from a working one until two
        machines disagree.
    """
    raw = cfg.get("project", {}).get("py_format_command")
    if raw is None:
        return []
    if isinstance(raw, str):
        raise ValueError(
            "[project] py_format_command must be a list of arguments, not a "
            f'string — write ["uv", "run", "ruff", "format"], not "{raw}". '
            "A string would have to be split, and jm will not guess where."
        )
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValueError(
            "[project] py_format_command must be a non-empty list of "
            f"arguments; got {raw!r}."
        )
    if not all(isinstance(a, str) for a in raw):
        raise ValueError(
            "[project] py_format_command must contain only strings; got "
            f"{raw!r}."
        )
    return [str(a) for a in raw]


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


def default_class_name(component: str) -> str:
    """Python class name for *component* when no ``class_name`` overrides it.

    The single source of truth for that derivation. Each underscore-separated
    word gets its first letter upper-cased and **the rest left alone**, so an
    id that already carries capitals survives intact.

    That last part is the whole point (gh-628). The C generators used this
    rule while the stub generator used ``str.title()``, which upper-cases the
    first letter and lower-cases every other one — identical for the
    all-lowercase ids jm was designed around, and destructive for anything
    else:

    - ``HalfbandDecimator`` -> ``Halfbanddecimator`` in the stub, while the
      extension defined ``HalfbandDecimator`` and the package re-exported it.
      The stub named a class that did not exist and omitted the one that did.
    - Only an id with a capital after the first character diverged, which is
      why it went unnoticed: ``fir_filter``, ``nco`` and ``acc_f32`` render
      identically under both rules.

    Every caller — the C type name, the ``.pyi`` class, the package
    re-export, ``jm view``'s name-collision check, ``jm remove``'s cleanup —
    must agree on one answer, so they all route here.

    >>> default_class_name("fir_filter")
    'FirFilter'
    >>> default_class_name("acc_f32")
    'AccF32'
    >>> default_class_name("HalfbandDecimator")
    'HalfbandDecimator'
    >>> default_class_name("my_FFT")
    'MyFFT'
    """
    return "".join(w[0].upper() + w[1:] for w in component.split("_") if w)


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


# ── Destructor declaration ([<comp>.destroy], gh-541 / gh-544) ──────────────
#
# One table rather than five scalars, because the keys only make sense
# together: `error`/`error_message` are meaningless without `returns = "int"`,
# and `aliases` is meaningless without knowing what `name` is. Grouping them
# also keeps the object surface honest — the whole teardown contract is one
# block a reader can take in at once.
#
# Manifest-only, no CLI flag — same call as `package` (gh-523). Five
# interacting keys is exactly the shape the CLI is the wrong tool for (see
# `_cli_object`'s division of labour: the CLI handles simple scalars, TOML
# handles anything with internal structure).

# The only value of `returns` that changes anything. Anything else is either
# the default (absent / "void") or an authoring error.
DESTROY_RETURNS = frozenset({"", "void", "int"})


def destroy_spec(cfg: dict, component: str) -> dict:
    """Return the component's ``[<comp>.destroy]`` table ({} if undeclared).

    Undeclared means the historical hardcoded behaviour: a Python method
    literally named ``destroy()``, a ``void`` C destructor, and an ``__exit__``
    that can never fail. Every render path collapses to byte-identical output
    in that case, which is what makes the table safe to introduce.

    Parameters
    ----------
    cfg : dict
        Loaded manifest.
    component : str
        Component id, e.g. ``wfm_writer``.

    Returns
    -------
    dict
        The raw table. Keys: ``name``, ``aliases``, ``returns``, ``error``,
        ``error_message``.

    Examples
    --------
    >>> destroy_spec({"w": {"destroy": {"name": "close"}}}, "w")
    {'name': 'close'}
    >>> destroy_spec({"w": {}}, "w")
    {}
    """
    spec = cfg.get(component, {}).get("destroy") or {}
    return dict(spec) if isinstance(spec, dict) else {}


def set_destroy_spec(cfg: dict, component: str, spec: dict) -> dict:
    """Store (or clear, when *spec* is falsy) a component's destroy table."""
    if spec:
        cfg.setdefault(component, {})["destroy"] = dict(spec)
    else:
        cfg.get(component, {}).pop("destroy", None)
    return cfg


def destroy_name(cfg: dict, component: str) -> str:
    """Python method name for teardown (default ``destroy``)."""
    return destroy_spec(cfg, component).get("name") or "destroy"


def destroy_aliases(cfg: dict, component: str) -> list[str]:
    """Additional Python names bound to the same C function ([] if none)."""
    return list(destroy_spec(cfg, component).get("aliases", []))


def destroy_returns_int(cfg: dict, component: str) -> bool:
    """True when the destructor is declared fallible (``returns = "int"``)."""
    return destroy_spec(cfg, component).get("returns") == "int"


def add_component(
    cfg: dict,
    component: str,
    vars_: list[tuple[str, str, str]],
    arg_type_: str = "float _Complex",
    return_type_: str | None = None,
    array_args_: list[tuple[str, str]] = (),
    no_state_: bool = False,
    no_step_: bool = False,
    no_reset_: bool = False,
    opaque_state_: bool = False,
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
    # non-streamable objects produce no golden-output churn. gh-542 keeps
    # `no_reset` in this opt-in group (unlike `no_step`, which predates the
    # convention and is always written) for the same reason.
    if no_reset_:
        entry["no_reset"] = "true"
    if opaque_state_:
        entry["opaque_state"] = "true"
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


def _toml_inline_string(value: str) -> str:
    """*value* as a TOML **basic string**, safe on one line, quotes included.

    `_str_assign` is the multi-line-capable sibling and cannot be used inside
    an inline table, which has no ``\"\"\"`` form. Hand-rolling the escape here
    was a mistake worth recording: escaping only ``\\``, ``"`` and ``\\n``
    leaves a raw carriage return in the output, which TOML forbids in a basic
    string. `_dump` self-checks with ``tomllib.loads`` and, on failure, returns
    the text anyway — so ``C.save`` wrote a manifest that ``C.load`` then
    refused, from prose as ordinary as a docstring lifted out of a CRLF file.

    ``json.dumps`` is the escape, not a reimplementation of one: JSON's string
    grammar is a subset of TOML's basic string for every character that needs
    escaping (``\\b \\t \\n \\f \\r \\" \\\\`` literally, everything else
    control as ``\\uXXXX``). ``ensure_ascii=False`` keeps an author's non-ASCII
    prose readable, which TOML accepts unescaped.
    """
    return _json.dumps(value, ensure_ascii=False)


def _init_param_pairs(p: dict) -> list[tuple[str, bool, str]]:
    """``(key, is_bool, value)`` for each key *p* actually carries.

    Presence is the **narrower** of the two old rules, deliberately: a bool key
    is written when truthy, everything else when present and non-empty. The
    old table form wrote a present-but-empty value (`if "default" in p`); the
    old inline form did not. `_project_init_params` reads an absent key and an
    empty one identically, so dropping it loses no meaning — and converging on
    the narrower rule is what lets the two syntaxes produce the same key set
    for the same manifest, which is the property this exists to give them.

    One consequence worth naming, since it is not local to the renderer: a
    hand-written `default = ""` now disappears on the first save. That makes
    `_round_trips`' `got == want` false for such a manifest, so the gh-698
    `_dump` fast path stops applying to it and every save falls back to
    tomlkit — slower, still correct. On the brand-new-file path, where `_dump`
    runs unguarded, the key is simply not written. Both are the right outcome
    for a key whose empty value means nothing, but neither is silent about it
    here.
    """
    out: list[tuple[str, bool, str]] = []
    for key, is_bool in _INIT_PARAM_FIELDS:
        val = p.get(key)
        if is_bool:
            if val:
                out.append((key, True, "true"))
        elif val not in (None, ""):
            out.append((key, False, str(val)))
    return out


def _init_param_block_lines(p: dict) -> list[str]:
    """One init-param as ``key = value`` lines under a ``[[…]]`` header.

    ``doc`` goes through `_str_assign` so multi-line prose keeps the readable
    ``\"\"\"`` form it has always had here; every other key is a plain quoted
    scalar or a bare boolean.
    """
    lines: list[str] = []
    for key, is_bool, val in _init_param_pairs(p):
        # Every scalar through `_str_assign`, not just `doc`. It already does
        # the quote/backslash escape correctly, and the bare f-string this
        # replaced could not survive its own output: a `default_raw` holding a
        # C expression with a string literal (`default_raw = 'a"b'`) emitted
        # `default = "a"b"`, which `C.load` then rejects with a
        # TOMLDecodeError. Ordinary values render byte-identically, so this is
        # zero churn for every manifest that was already fine.
        lines.append(
            "{} = true".format(key) if is_bool else _str_assign(key, val)
        )
    return lines


def _init_param_inline(p: dict) -> str:
    """One init-param as a TOML **inline table**, for a view's ``init_params``.

    `doc` is emitted as a single-line basic string even when the prose has
    newlines: an inline table cannot hold TOML's ``\"\"\"`` form, and dropping
    the key — which is what happened before — silently loses an author's
    documentation. `_str_assign`'s escaping is not reused here for that
    reason; this needs the always-inline spelling.
    """
    parts = []
    for key, is_bool, val in _init_param_pairs(p):
        if is_bool:
            parts.append(f"{key} = true")
        else:
            parts.append(f"{key} = {_toml_inline_string(val)}")
    return "{" + ", ".join(parts) + "}"


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
        "count_default",
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


def _inline_result_field(f: dict) -> str:
    """One ``result_fields`` entry as a TOML inline table.

    Shared by the method dump and the free-function dump. gh-646 added a ``doc``
    key to this table, and those are two separate emit sites — the exact shape
    that lets a manifest key be honoured on one path and silently dropped on
    the other, which is the class `tests/test_manifest_wiring_gate.py` exists
    to catch. One writer, so there is nothing to keep in step.

    Examples
    --------
    >>> _inline_result_field({"name": "enob", "type": "double"})
    '{name = "enob", type = "double"}'
    >>> _inline_result_field({"name": "n", "type": "int", "doc": 'say "hi"'})
    '{name = "n", type = "int", doc = "say \\\\"hi\\\\""}'
    """
    parts = [f'name = "{f["name"]}"', f'type = "{f["type"]}"']
    if f.get("doc"):
        body = (
            str(f["doc"])
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            # An inline table is one line; a wrapped doc rides as an escape.
            .replace("\n", "\\n")
        )
        parts.append(f'doc = "{body}"')
    return "{" + ", ".join(parts) + "}"


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

    Every optional key a property can carry must be emitted here. This dumper
    is bypassed on an ordinary save — ``_write_doc`` round-trips an *existing*
    file through tomlkit, which preserves keys generically — so an omission is
    invisible until ``jm split-objects`` or ``jm migrate`` rewrites the section
    from the parsed dict. gh-549: ``enum`` was missing, and splitting a project
    reverted an enum property's Python face from its ordered string back to a
    raw int (and dropped the gh-521 bounds check) with no error and no warning.
    ``tests/test_gh549_property_dump_keys.py`` pins the whole key set rather
    than any one key, so the next key added cannot repeat it.
    """
    lines = [header, f'name = "{p["name"]}"']
    if p.get("doc"):
        lines.append(_doc_assign(p["doc"]))
    lines.append(f'type = "{p.get("type") or p.get("ctype", "size_t")}"')
    # gh-519: `enum` qualifies how `type` is presented to Python, so it reads
    # best directly beneath it.
    if p.get("enum"):
        lines.append(f'enum = "{p["enum"]}"')
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
    # gh-543: a container property's accessors.
    if p.get("value_type"):
        lines.append(f'value_type = "{p["value_type"]}"')
    for _fn in ("count_fn", "key_fn", "value_fn"):
        if p.get(_fn):
            lines.append(f'{_fn} = "{p[_fn]}"')
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
    if m.get("count_default"):
        lines.append(_str_assign("count_default", m["count_default"]))
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
            s = f'name = "{p["name"]}"'
            # gh-554: a codec `role = "variant"` param carries no C type (jm
            # packs it from a PyObject), so `type` is optional here.
            if p.get("type"):
                s += f', type = "{p["type"]}"'
            # gh-240: an optional scalar default round-trips as a string.
            if p.get("default") not in (None, ""):
                s += f', default = "{p["default"]}"'
            # gh-554: the codec arg role (discriminant / variant) must survive
            # save()/load() — a per-param key the gh-257 generic passthrough
            # (method-level scalars only) does not cover.
            if p.get("role"):
                s += f', role = "{p["role"]}"'
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
            _inline_result_field(f) for f in m["result_fields"]
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
            elif isinstance(v, (list, tuple)):
                # gh-763: this used to be a hand-maintained name list
                # (`c_deps`, `find_packages`, `pkg_modules`, `platforms`), so
                # any *other* list-valued key fell through to the scalar
                # branch and was written as the Python repr of a list inside
                # quotes — `c_format_command = "['clang-format']"`, which
                # reloads as a string and raises. Asking the value what it is
                # needs no registration, and a new list-valued key cannot be
                # forgotten (see also `status_allow`, mangled the same way).
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
                        # gh-537: dropping this republishes a test-only dep
                        # into the artifact on the next apply.
                        if d.get("test_only"):
                            inner += ", test_only = true"
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
        # gh-523: `package` is not capsule/handle-specific — an object module
        # can also land its .so / .pyi / __init__ exports inside a sibling
        # package. The capsule branch above already emitted it (in its own
        # key order), so only the non-capsule kinds need it here or the key
        # would be silently dropped on the next save.
        if data.get("kind") not in ("capsule", "composer", "handle"):
            if data.get("package"):
                lines.append(f'package = "{data["package"]}"')
        # gh-645: applies to every module kind -- a capsule's free functions
        # need documenting as much as an object group's.
        if data.get("doc"):
            lines.append(_str_assign("doc", str(data["doc"])))
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
                    _inline_result_field(f) for f in fn["result_fields"]
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
            # gh-542: must round-trip through _dump or `jm apply` silently
            # drops the key and regenerates the reset() the manifest asked
            # to have removed.
            "no_reset",
            # gh-588: same reasoning — dropping it would republish the whole
            # struct in the public header on the next apply.
            "opaque_state",
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
                    if d.get("test_only"):  # gh-537, see above
                        inner += ", test_only = true"
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
        # gh-541/gh-544: the destructor contract. Emitted before the [[...]]
        # sub-tables purely for readability — TOML headers are absolute paths,
        # so a following [[<comp>.state]] still binds to <comp>. Nothing is
        # emitted when undeclared, so existing manifests are unchanged.
        _destroy = comp_data.get("destroy") or {}
        if _destroy:
            lines.append(f"[{comp}.destroy]")
            if _destroy.get("name"):
                lines.append(f'name = "{_destroy["name"]}"')
            if _destroy.get("aliases"):
                _al = ", ".join(f'"{a}"' for a in _destroy["aliases"])
                lines.append(f"aliases = [{_al}]")
            if _destroy.get("returns"):
                lines.append(f'returns = "{_destroy["returns"]}"')
            if _destroy.get("error"):
                lines.append(f'error = "{_destroy["error"]}"')
            if _destroy.get("error_message"):
                lines.append(
                    _str_assign("error_message", _destroy["error_message"])
                )
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
            lines += _init_param_block_lines(p)
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
                parts = ", ".join(
                    _init_param_inline(p) for p in v["init_params"]
                )
                lines.append(f"init_params = [{parts}]")
            if v.get("exclude_properties"):
                ep = ", ".join(f'"{n}"' for n in v["exclude_properties"])
                lines.append(f"exclude_properties = [{ep}]")
            if v.get("exclude_methods"):
                em = ", ".join(f'"{n}"' for n in v["exclude_methods"])
                lines.append(f"exclude_methods = [{em}]")
            # gh-580: a view's OWN create_error, when it overrides rather than
            # inheriting the parent's. Only the explicitly-declared keys are
            # written — dumping the resolved value would freeze an inherited
            # translation into a copy that then stops tracking the parent.
            if v.get("create_error"):
                lines.append(f'create_error = "{v["create_error"]}"')
                lines.append(
                    _str_assign(
                        "create_error_message",
                        v.get("create_error_message", ""),
                    )
                )
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

    text = "\n".join(lines)

    # gh-763: everything above renders a section kind `_dump` was taught about,
    # one branch at a time. A kind nobody taught it — `[codec.X]` is the one
    # doppler has — is not rendered, and the omission is silent.
    #
    # `_round_trips` was meant to contain that, but it only guards the *update*
    # paths. `save` calls `_dump` unguarded when the file does not exist yet,
    # and again when tomlkit is unavailable — which is a real environment, not
    # a hypothetical: just-buildit does not propagate `[project].dependencies`
    # to the wheel, so a tool-installed jm may have no tomlkit at all. In that
    # environment every root-writing command silently deleted the whole codec
    # table.
    #
    # So `_dump` closes its own gap instead of trusting a caller to notice:
    # parse what was just written, and anything from *cfg* that did not survive
    # is appended generically. Asking the output what is missing needs no list
    # of known kinds, and cannot be forgotten for the kind added next — the
    # same reason the array branch above tests the value instead of its name.
    try:
        survived = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return text

    # Absent entirely -> append it generically. `[codec.X]` is this case: the
    # component loop above does not claim it, so nothing was written at all.
    missing = [k for k in cfg if k not in survived]
    if missing:
        text = text.rstrip("\n") + "\n\n"
        text += "\n".join(_dump_generic(k, cfg[k]) for k in missing)
        survived = tomllib.loads(text)

    # Present but WRONG is the other half, and it cannot be repaired by
    # appending: the section header is already in the file, and TOML forbids a
    # second one. The component loop claims any unrecognised top-level table
    # and emits a bare `[name]` with none of its keys — so the key survives,
    # empty, and a presence check reads that as success. Found by the tests
    # for this very fix, which is the argument for writing them.
    #
    # Raising is the only honest option left. On the guarded path the caller
    # never gets here (`_round_trips` has already sent it to tomlkit); on the
    # unguarded ones a hard error beats a manifest that silently lost a
    # section, which is the failure gh-763 exists to end.
    # Narrowly: emitted *empty* while cfg has content. A full equality test
    # here is wrong — a component section legitimately does not compare byte
    # equal after a round trip, which is exactly why `_round_trips` is a
    # fall-back guard and not an assertion. Demanding equality raised on 143
    # ordinary tests before this was narrowed, which is the useful correction:
    # "does not round-trip exactly" and "silently lost its contents" are
    # different facts, and only the second one is a bug.
    _empty = ({}, [])
    mangled = [
        k
        for k in cfg
        if k in survived and survived[k] in _empty and cfg[k] not in _empty
    ]
    if mangled:
        raise ValueError(
            "jm cannot faithfully serialise "
            + ", ".join(f"[{k}]" for k in mangled)
            + " and will not write a manifest that loses it. Please report"
            " this section's shape (jm-763)."
        )
    return text


def _toml_scalar(v: object) -> str:
    """*v* as a TOML scalar literal."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return '"{}"'.format(str(v).replace("\\", "\\\\").replace('"', '\\"'))


def _toml_value(v: object) -> str:
    """*v* as a TOML value — scalar, array, or inline table.

    Covers what a manifest section actually holds: scalars, arrays of
    scalars, and arrays of inline tables (``[codec.X] entries``). Nesting
    deeper than this has never appeared in a manifest, and if it ever does
    the round-trip check in :func:`_dump` is what will say so rather than
    the file quietly losing it.
    """
    if isinstance(v, dict):
        inner = ", ".join(f"{k} = {_toml_value(x)}" for k, x in v.items())
        return "{ " + inner + " }"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return _toml_scalar(v)


def _dump_generic(name: str, value: object) -> str:
    """A top-level key rendered without knowing what kind of section it is."""
    if isinstance(value, list):
        return "\n".join(
            f"[[{name}]]\n"
            + "\n".join(f"{k} = {_toml_value(x)}" for k, x in item.items())
            + "\n"
            for item in value
            if isinstance(item, dict)
        )
    if not isinstance(value, dict):
        return f"{name} = {_toml_value(value)}\n"
    # A table of tables (`[codec.blue_keyword]`) versus a plain table.
    if value and all(isinstance(x, dict) for x in value.values()):
        return "\n".join(
            f"[{name}.{sub}]\n"
            + "\n".join(f"{k} = {_toml_value(x)}" for k, x in body.items())
            + "\n"
            for sub, body in value.items()
        )
    body = "\n".join(f"{k} = {_toml_value(x)}" for k, x in value.items())
    return f"[{name}]\n{body}\n"

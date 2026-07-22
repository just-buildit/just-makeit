"""
_bind.py — ``just-makeit bind`` command.

Reads ``<comp>_core.h`` and synthesises ``<comp>_ext.c`` (plus the
matching ``.pyi``) without consulting ``just-makeit.toml``.  Designed
as the header-driven path described in
``docs/developers/bind-design.md``.

Supported shapes (Phase 3b)
---------------------------
- State struct with scalar fields and/or opaque pointer fields.
- Constructor taking state fields in order; ctor params not matching
  a state field become init_params.
- Inline ``<comp>_step()`` with one scalar arg and scalar return.
- Getter/setter pairs (``<comp>_get_<field>`` /
  ``<comp>_set_<field>``) → Python properties.
- Custom verbs: any other ``<comp>_<verb>(state, ...)`` declaration
  whose return type and (optionally) single scalar arg can be parsed.
- Variable-output methods: ``<comp>_<verb>`` paired with a
  ``<comp>_<verb>_max_out`` sibling declaration.
- Opaque state: forward-declared struct (no ``{ ... }`` body in the
  header); skip field discovery.

When a declaration is found but cannot be parsed (unknown type, complex
multi-param signature), it is skipped with a warning rather than a hard
error — the user can add those methods via TOML.

Package name comes from ``pyproject.toml`` in the project root.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from . import _config as C
from . import _context as Ctx
from . import _render as R
from . import _stubs as S
from . import _types as T
from ._init import _make_component_ctx


_STATE_STRUCT_RE = re.compile(
    r"typedef\s+struct\s*\{([^}]*)\}\s*(\w+)_state_t\s*;",
    re.DOTALL,
)

# One field per line: "<type> <name>;"  (allows multi-word types like
# "float _Complex", "unsigned long", and "long long").
_FIELD_RE = re.compile(
    r"^\s*((?:\w+\s+)*\w+(?:\s+_Complex)?)\s+(\w+)\s*;\s*$",
    re.MULTILINE,
)

# Inline step: "static inline RET <comp>_step([const] <comp>_state_t *state, ARG x)".
# ``const`` is optional — a Welford-style step that mutates state through
# the pointer drops the qualifier.
_STEP_RE = re.compile(
    r"static\s+inline\s+([\w\s]+?)\s*"
    r"(\w+)_step\s*\(\s*"
    r"(?:const\s+)?\w+_state_t\s*\*\s*\w+\s*,?\s*"
    r"([^)]*)\)",
    re.MULTILINE,
)

# create signature: "<comp>_state_t *<comp>_create(...);"
_CREATE_RE = re.compile(
    r"(\w+)_state_t\s*\*\s*\w+_create\s*\(\s*([^)]*)\)\s*;",
)

# In <comp>_core.c's reset body: ``state->FIELD = DEFAULT;``.  Captures
# the field name and the literal we should use as the Python ctor default.
_RESET_ASSIGN_RE = re.compile(
    r"state->(\w+)\s*=\s*([^;]+);",
)

# ── Phase 3b patterns ─────────────────────────────────────────────────────────

# Forward-declared struct (opaque state — no body in header):
#   typedef struct <comp>_state_t <comp>_state_t;
_OPAQUE_FWD_RE = re.compile(
    r"typedef\s+struct\s+(\w+)_state_t\s+\1_state_t\s*;",
)

# Getter declaration:
#   <ctype>  <comp>_get_<field>(const <comp>_state_t *state);
_GETTER_DECL_RE = re.compile(
    r"^\s*([\w\s]+?)\s+(\w+)_get_(\w+)\s*\(\s*(?:const\s+)?\w+_state_t\s*\*[^)]*\)\s*;",
    re.MULTILINE,
)

# Setter declaration:
#   void  <comp>_set_<field>(<comp>_state_t *state, <ctype> val);
_SETTER_DECL_RE = re.compile(
    r"^\s*void\s+(\w+)_set_(\w+)\s*\(\s*\w+_state_t\s*\*[^,)]+,\s*([\w\s]+?)\s+\w+\s*\)\s*;",
    re.MULTILINE,
)

# Variable-output max_out sibling:
#   size_t  <comp>_<verb>_max_out(<comp>_state_t *state);
_MAX_OUT_DECL_RE = re.compile(
    r"size_t\s+(\w+)_(\w+)_max_out\s*\(\s*(?:const\s+)?\w+_state_t\s*\*[^)]*\)\s*;",
    re.MULTILINE,
)

# General method declaration — everything of the form:
#   <RET>  <comp>_<verb>(<comp>_state_t *state[, <single-scalar-arg>]);
# We only try to parse methods whose arg list (after the state pointer) is
# either empty or a single simple scalar.  Anything more complex is skipped.
_SIMPLE_METHOD_RE = re.compile(
    r"^\s*([\w\s\*]+?)\s+(\w+)_(\w+)\s*\(\s*(?:const\s+)?\w+_state_t\s*\*\s*\w+"
    r"(?:\s*,\s*([\w\s]+?)\s+\w+)?\s*\)\s*;",
    re.MULTILINE,
)

# Variable-output method: size_t <comp>_<verb>(state *s[, in_t in], out_t *out);
# Captures comp, verb, and optionally the scalar input type.
_VAR_OUT_DECL_RE = re.compile(
    r"size_t\s+(\w+)_(\w+)\s*\(\s*(?:const\s+)?\w+_state_t\s*\*\s*\w+"
    r"(?:\s*,\s*([\w\s]+?)\s+\w+)?"  # optional scalar input arg
    r"\s*,\s*[\w\s]+?\*\s*\w+\s*\)\s*;",  # mandatory output pointer (any ptr)
    re.MULTILINE,
)

# ── Lifecycle verbs that the parser skips when collecting custom methods ──────
_LIFECYCLE_VERBS: frozenset[str] = frozenset(
    {"create", "destroy", "reset", "step", "steps", "step_batch"}
)


def _normalize_ctype(s: str) -> str:
    """Collapse internal whitespace; map ``float complex`` back to
    ``float _Complex`` so it matches ``_CTYPE_META`` keys.
    """
    s = " ".join(s.strip().split())
    if s.endswith("complex") and "_Complex" not in s:
        s = s.replace("complex", "_Complex")
    return s


def parse_header(path: Path) -> dict:
    """Extract component shape from a header following the jm template contract.

    Returns a dict with keys::

        component   str
        fields      list of (name, ctype) — scalar fields in struct order
        arg_type    str   (scalar C type, e.g. "float _Complex")
        return_type str
        properties  list of {"name", "type", "writable"} dicts
        methods     list of {"name", "arg_type", "return_type", "variable_output"} dicts
        init_params list of (name, ctype, default) triples — ctor params not
                    matching a state field (best-effort; empty if not parseable)
        is_opaque   bool — True when state is forward-declared (no struct body)

    Raises ``ValueError`` when the header cannot be parsed at all (e.g. no
    state struct, missing step()).  Skips individual methods it cannot parse
    without raising.
    """
    import warnings

    text = path.read_text(encoding="utf-8")

    # ── Detect opaque (forward-decl only, no struct body) ─────────────────
    m_opaque = _OPAQUE_FWD_RE.search(text)
    m_state = _STATE_STRUCT_RE.search(text)
    is_opaque = bool(m_opaque) and not bool(m_state)

    if is_opaque:
        comp = m_opaque.group(1)
        fields: list[tuple[str, str]] = []
    else:
        if not m_state:
            raise ValueError(
                f"{path}: no `typedef struct {{ ... }} <comp>_state_t;` block"
            )
        struct_body, comp = m_state.group(1), m_state.group(2)

        field_pairs = _FIELD_RE.findall(struct_body)
        if not field_pairs:
            raise ValueError(
                f"{path}: state struct has no parseable scalar fields"
            )
        fields = []
        for raw_ct, name in field_pairs:
            ct = _normalize_ctype(raw_ct)
            if ct not in T._CTYPE_META:
                raise ValueError(
                    f"{path}: field '{name}': unsupported type '{ct}'"
                    f" — use TOML for opaque/pointer fields"
                )
            fields.append((name, ct))

    field_names: frozenset[str] = frozenset(n for n, _ in fields)

    # ── Step signature ─────────────────────────────────────────────────────
    m_step = _STEP_RE.search(text)
    if not m_step:
        raise ValueError(
            f"{path}: no inline `static inline <RET> <comp>_step(...)` found"
        )
    ret_raw, step_comp, arg_decl = (
        m_step.group(1),
        m_step.group(2),
        m_step.group(3),
    )
    if step_comp != comp:
        raise ValueError(
            f"{path}: step prefix '{step_comp}' != state prefix '{comp}'"
        )
    return_type = _normalize_ctype(ret_raw)
    if return_type not in T._CTYPE_META:
        raise ValueError(
            f"{path}: step return type '{return_type}' not supported"
        )

    arg_decl_norm = " ".join(arg_decl.strip().split())
    arg_parts = arg_decl_norm.rsplit(" ", 1)
    if len(arg_parts) != 2:
        raise ValueError(f"{path}: cannot parse step arg from '{arg_decl}'")
    arg_type = _normalize_ctype(arg_parts[0])
    if arg_type not in T._CTYPE_META:
        raise ValueError(f"{path}: step arg type '{arg_type}' not supported")

    # ── Init_params: ctor params not matching a state field name ──────────
    init_params: list[tuple[str, str, str]] = []
    m_create = _CREATE_RE.search(text)
    if m_create and m_create.group(1) == comp:
        raw_params = m_create.group(2).strip()
        if raw_params and raw_params != "void":
            for raw_param in raw_params.split(","):
                raw_param = raw_param.strip()
                parts = raw_param.rsplit(None, 1)
                if len(parts) == 2:
                    ptype = _normalize_ctype(parts[0])
                    pname = parts[1].lstrip("*").strip()
                    if pname not in field_names and ptype in T._CTYPE_META:
                        zero = T._CTYPE_META[ptype]["zero"]
                        init_params.append((pname, ptype, zero))

    # ── Properties: getter/setter declaration pairs ────────────────────────
    getters: dict[str, str] = {}  # field_name -> ctype
    for m in _GETTER_DECL_RE.finditer(text):
        ret, gcomp, field = m.group(1), m.group(2), m.group(3)
        if gcomp != comp:
            continue
        ct = _normalize_ctype(ret)
        if ct in T._CTYPE_META:
            getters[field] = ct

    setters: set[str] = set()  # field names with a setter
    for m in _SETTER_DECL_RE.finditer(text):
        scomp, field = m.group(1), m.group(2)
        if scomp == comp:
            setters.add(field)

    # State-field getters/setters are generated by make_state_ctx; only
    # include getters for fields that are NOT in the state struct.
    properties: list[dict] = [
        {"name": field, "type": ct, "writable": field in setters}
        for field, ct in getters.items()
        if field not in field_names
    ]

    # ── Variable-output: _max_out sibling declarations ─────────────────────
    var_output_verbs: set[str] = set()
    for m in _MAX_OUT_DECL_RE.finditer(text):
        if m.group(1) == comp:
            var_output_verbs.add(m.group(2))

    # ── Custom methods: remaining <comp>_<verb> declarations ──────────────
    # Collect verbs already claimed (lifecycle, getters, setters, max_out
    # siblings, and any variant of "steps" the template generates).
    claimed: set[str] = set(
        _LIFECYCLE_VERBS
        | {f"get_{f}" for f in getters}
        | {f"set_{f}" for f in setters}
        | {f"{v}_max_out" for v in var_output_verbs}
        | {"steps", "step_batch", "max_out"}
    )

    methods: list[dict] = []
    for m in _SIMPLE_METHOD_RE.finditer(text):
        ret_raw_m, mcomp, verb, arg_raw = (
            m.group(1),
            m.group(2),
            m.group(3),
            m.group(4),
        )
        if mcomp != comp or verb in claimed:
            continue
        claimed.add(verb)

        ret_ct = _normalize_ctype(ret_raw_m)
        if ret_ct not in T._CTYPE_META and ret_ct != "void":
            warnings.warn(
                f"jm bind: skipping method '{comp}_{verb}' — "
                f"return type '{ret_ct}' not in type allowlist",
                stacklevel=2,
            )
            continue

        # Arg type: group(4) captures only the type token (the identifier
        # is consumed by \s+\w+ in the regex and not captured).
        if arg_raw is None:
            marg = "void"
        else:
            marg = _normalize_ctype(arg_raw.strip())
            if marg not in T._CTYPE_META and marg != "void":
                warnings.warn(
                    f"jm bind: skipping method '{comp}_{verb}' — "
                    f"arg type '{marg}' not in type allowlist "
                    f"(use TOML for array or multi-param methods)",
                    stacklevel=2,
                )
                continue

        entry: dict = {
            "name": verb,
            "arg_type": marg,
            "return_type": ret_ct,
        }
        if verb in var_output_verbs:
            entry["variable_output"] = True
        methods.append(entry)

    # ── Second pass: variable-output methods with output-pointer signatures ──
    # Pick up verbs detected by _MAX_OUT_DECL_RE that _SIMPLE_METHOD_RE
    # missed (e.g. comp_verb(state, in_t x, out_t *out) — two args after
    # state exceeds the simple pattern).
    already_emitted: set[str] = {m["name"] for m in methods}
    for verb in sorted(var_output_verbs):
        if verb in already_emitted or verb in claimed:
            continue
        # Try the variable-output-specific pattern.
        decl_re = re.compile(
            rf"size_t\s+{re.escape(comp)}_{re.escape(verb)}\s*"
            rf"\(\s*(?:const\s+)?\w+_state_t\s*\*\s*\w+"
            rf"(?:\s*,\s*([\w\s]+?)\s+\w+)?"  # optional scalar in
            rf"\s*,\s*[\w\s]+?\*\s*\w+\s*\)\s*;",
            re.MULTILINE,
        )
        m_vo = decl_re.search(text)
        if m_vo is None:
            continue
        in_raw = m_vo.group(1)
        if in_raw is None:
            vo_arg = "void"
        else:
            vo_arg = _normalize_ctype(in_raw.strip())
            if vo_arg not in T._CTYPE_META:
                vo_arg = "void"  # fall back; renderer will use void-arg shape
        methods.append(
            {
                "name": verb,
                "arg_type": vo_arg,
                "return_type": "void",  # variable-output: actual element type
                # comes from the out pointer, which we don't parse here;
                # the renderer uses the `variable_output` flag to allocate
                # the buffer and return a numpy view.
                "variable_output": True,
            }
        )

    return {
        "component": comp,
        "fields": fields,
        "arg_type": arg_type,
        "return_type": return_type,
        "properties": properties,
        "methods": methods,
        "init_params": init_params,
        "is_opaque": is_opaque,
    }


def parse_reset_defaults(core_c: Path) -> dict[str, str]:
    """Pull ``state->FIELD = DEFAULT;`` literals out of ``<comp>_reset``.

    Returns an empty dict when the file is absent or the reset body
    cannot be located — callers should fall back to the type's zero
    literal.  This is a best-effort enhancement, not a hard requirement.
    """
    if not core_c.exists():
        return {}
    text = core_c.read_text(encoding="utf-8")
    # Find the reset function body — look for ``_reset(...)`` followed by
    # an opening brace and capture until the matching close brace.  For
    # simple bodies (no nested braces) a non-greedy match suffices.
    m = re.search(
        r"\w+_reset\s*\([^)]*\)\s*\{([^}]*)\}",
        text,
    )
    if not m:
        return {}
    return {
        name: value.strip()
        for name, value in _RESET_ASSIGN_RE.findall(m.group(1))
    }


def _read_pkg(root: Path) -> str:
    """Read the project package name from pyproject.toml.

    Falls back to the project directory name when pyproject is absent.
    """
    pp = root / "pyproject.toml"
    if pp.exists():
        try:
            import tomllib
        except ModuleNotFoundError:  # Python < 3.11
            import tomli as tomllib

        with pp.open("rb") as fh:
            data = tomllib.load(fh)
        name = data.get("project", {}).get("name")
        if name:
            return name.replace("-", "_")
    return root.name.replace("-", "_")


def _build_ctx(
    comp: str,
    parsed: dict,
    pkg: str,
    defaults: dict[str, str] | None = None,
) -> dict:
    """Build the same context dict ``_init.run`` produces for a component."""
    ctx = _make_component_ctx(comp)
    ctx.update(
        {
            "package": pkg,
            "PACKAGE": pkg.upper(),
            "project": pkg.replace("_", "-"),
            "project_underscore": pkg,
            "version": "0.0.0",
        }
    )

    arg_type = parsed["arg_type"]
    return_type = parsed["return_type"]
    is_opaque = parsed.get("is_opaque", False)
    init_params = parsed.get("init_params", [])

    # State vars: (name, ctype, default).  Prefer the literal extracted
    # from <comp>_core.c's reset() body; fall back to the type's zero
    # literal when reset isn't parseable.
    defaults = defaults or {}
    state_vars = [
        (name, ct, defaults.get(name, T._CTYPE_META[ct]["zero"]))
        for name, ct in parsed["fields"]
    ]

    ctx.update(Ctx.make_sample_ctx(arg_type, return_type))
    ctx.update(
        Ctx.make_state_ctx(
            ctx["component"],
            ctx["Component"],
            state_vars,
            no_state=is_opaque,
            init_params=init_params,
        )
    )
    ctx.update(Ctx.make_perf_ctx(False))
    ctx.update(Ctx.make_step_ctx(ctx, arg_type, return_type))
    ctx.update(
        Ctx.make_methods_ctx(
            ctx["component"],
            ctx["Component"],
            parsed.get("methods", []),
            pkg=pkg,
            py_create_args=ctx.get("py_create_args", ""),
            no_state=is_opaque,
            serializable=C._truthy(parsed.get("serializable")),
        )
    )
    ctx.update(
        Ctx.make_properties_ctx(
            ctx["component"],
            ctx["Component"],
            parsed.get("properties", []),
            frozenset(n for n, _, _ in state_vars),
            # gh-519: no `enums=` here. `bind` reflects a hand-written _core.h,
            # not the manifest, so there is no [[enum]] registry in scope and
            # `parsed` properties never carry an `enum` key. Leaving the
            # default (None) means enum support is simply absent on this path
            # and the render stays byte-identical (same reasoning as the
            # empty make_warnings_ctx below).
        )
    )
    # gh-481: `bind` reflects a hand-written _core.h rather than the manifest,
    # and a warning condition is authored intent — not recoverable from a
    # header. So this resolves empty, rendering exactly as `bind` did before.
    # Declaring warnings on a bound component would mean teaching `parse` to
    # carry them; not wired today.
    ctx.update(Ctx.make_warnings_ctx(ctx["component"], ctx["Component"], []))
    # gh-482: same reasoning — a create_error is authored intent, not something
    # recoverable from a header, so `bind` renders the MemoryError fallback
    # exactly as it did before.
    ctx.update(Ctx.make_errors_ctx(ctx["component"]))
    return ctx


def run(root: Path, component: str, *, write: bool = True) -> str:
    """Reflect ``<component>_core.h`` and (optionally) write ``_ext.c``.

    Returns the rendered text either way so tests and ``--check`` mode
    can compare without touching the filesystem.
    """
    header = root / "native" / "inc" / component / f"{component}_core.h"
    if not header.exists():
        print(f"error: header not found: {header}", file=sys.stderr)
        sys.exit(1)

    try:
        parsed = parse_header(header)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        print(
            "hint: jm bind handles scalar-state and opaque-state components"
            " following the template contract. For array args, multi-param"
            " methods, or non-standard naming, add those entries to TOML and"
            " use `jm apply`. See docs/developers/bind-design.md.",
            file=sys.stderr,
        )
        sys.exit(1)

    if parsed["component"] != component:
        print(
            f"error: header declares component '{parsed['component']}',"
            f" but you asked for '{component}'.",
            file=sys.stderr,
        )
        sys.exit(1)

    pkg = _read_pkg(root)
    core_c = root / "native" / "src" / component / f"{component}_core.c"
    defaults = parse_reset_defaults(core_c)
    ctx = _build_ctx(component, parsed, pkg, defaults)
    text = R.render(R.COMPONENT_EXT_C, ctx)

    if write:
        ext_c = root / "native" / "src" / component / f"{component}_ext.c"
        ext_c.parent.mkdir(parents=True, exist_ok=True)
        verb = "update" if ext_c.exists() else "create"
        ext_c.write_text(text, encoding="utf-8")
        print(f"  {verb}  {ext_c}")

        pyi = root / "src" / pkg / f"{component}.pyi"
        if pyi.exists() or (root / "src" / pkg).is_dir():
            pyi_text = R.render(R.COMPONENT_PYI, ctx)
            # gh-428: bind derives its output purely from the header and
            # otherwise never consults just-makeit.toml, but a manual_stub
            # method's hand-written .pyi text has no header declaration for
            # bind to reconstruct. Read the manifest (if any) only to learn
            # which symbols to preserve verbatim across this regen.
            if pyi.exists():
                cfg_path = root / C.FILENAME
                old_cfg = C.load(root) if cfg_path.exists() else {}
                pyi_text = S._splice_manual_stub_bodies(
                    old_cfg, pyi.read_text(encoding="utf-8"), pyi_text
                )
            pyi.parent.mkdir(parents=True, exist_ok=True)
            verb = "update" if pyi.exists() else "create"
            pyi.write_text(pyi_text, encoding="utf-8")
            print(f"  {verb}  {pyi}")
    return text

"""
_bind.py — ``just-makeit bind`` command (MVP / proof-of-concept).

Reads ``<comp>_core.h``, recognises the *filter* template shape, and
synthesises ``<comp>_ext.c`` (plus the matching ``.pyi``) without
consulting ``just-makeit.toml``.  Designed as the front-end demo for the
larger ``jm bind`` design captured in
``docs/developers/bind-design.md``.

Scope of this prototype
-----------------------
- Filter shape only: state struct with scalar fields, ``<comp>_create``
  taking those fields in order, an inline ``<comp>_step`` with one
  scalar arg returning one scalar.
- No methods, no properties, no init_params, no opaque state,
  no out_type, no variable_output.
- Package name comes from ``pyproject.toml`` in the project root.

If any of the above doesn't hold, the parser raises ``ValueError`` and
the caller falls back to the usual TOML-driven flow.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from . import _context as Ctx
from . import _render as R
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


def _normalize_ctype(s: str) -> str:
    """Collapse internal whitespace; map ``float complex`` back to
    ``float _Complex`` so it matches ``_CTYPE_META`` keys.
    """
    s = " ".join(s.strip().split())
    if s.endswith("complex") and "_Complex" not in s:
        s = s.replace("complex", "_Complex")
    return s


def parse_header(path: Path) -> dict:
    """Extract component, state fields, and step signature from a header.

    Returns a dict with keys::

        component   str
        fields      list of (name, ctype) — order as declared in the struct
        arg_type    str   (scalar C type, e.g. "float _Complex")
        return_type str

    Raises ``ValueError`` when the file doesn't match the filter shape.
    """
    text = path.read_text(encoding="utf-8")

    m_state = _STATE_STRUCT_RE.search(text)
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
    fields: list[tuple[str, str]] = []
    for raw_ct, name in field_pairs:
        ct = _normalize_ctype(raw_ct)
        if ct not in T._CTYPE_META:
            raise ValueError(
                f"{path}: field '{name}': unsupported type '{ct}'"
                f" (prototype only handles scalar types in _CTYPE_META)"
            )
        fields.append((name, ct))

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

    # arg_decl is e.g. "float _Complex x"; we only need the type half.
    arg_decl_norm = " ".join(arg_decl.strip().split())
    # Drop the final identifier (the arg name).
    arg_parts = arg_decl_norm.rsplit(" ", 1)
    if len(arg_parts) != 2:
        raise ValueError(f"{path}: cannot parse step arg from '{arg_decl}'")
    arg_type = _normalize_ctype(arg_parts[0])
    if arg_type not in T._CTYPE_META:
        raise ValueError(f"{path}: step arg type '{arg_type}' not supported")

    return {
        "component": comp,
        "fields": fields,
        "arg_type": arg_type,
        "return_type": return_type,
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
        import tomllib

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
    """Build the same context dict ``_init.run`` produces for a filter."""
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
        )
    )
    ctx.update(Ctx.make_perf_ctx(False))
    ctx.update(Ctx.make_step_ctx(ctx, arg_type, return_type))
    ctx.update(
        Ctx.make_methods_ctx(
            ctx["component"],
            ctx["Component"],
            [],
            pkg=pkg,
            py_create_args=ctx.get("py_create_args", ""),
        )
    )
    ctx.update(
        Ctx.make_properties_ctx(
            ctx["component"],
            ctx["Component"],
            [],
            frozenset(n for n, _, _ in state_vars),
        )
    )
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
            "hint: this prototype only handles the default filter shape."
            " Use `jm object` or a TOML manifest for other shapes.",
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
            pyi.parent.mkdir(parents=True, exist_ok=True)
            verb = "update" if pyi.exists() else "create"
            pyi.write_text(pyi_text, encoding="utf-8")
            print(f"  {verb}  {pyi}")
    return text

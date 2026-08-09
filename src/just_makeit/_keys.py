"""Recognise the keys a manifest table may carry, and say so when it does not.

jm tolerates an unknown scalar key on a manifest table by design: gh-257 makes
:func:`_config._dump` round-trip any key it does not itself emit, so a
hand-authored manifest survives ``save()`` -> ``load()`` instead of being
silently rewritten. That tolerance is deliberate and stays.

What was missing is that the tolerance was also **silent**. A key that belongs
to a different kind of table — ``check_return``, which ``jm function`` reads
and ``jm method`` does not — is accepted, preserved, and never acted on. The
author writes the intent down in a plausible spelling, jm says nothing, exits
0, and generates a binding that does not do it (gh-816). Both reported cases
were someone reaching for the right idea with the wrong key, so naming the kind
the key *is* valid for is most of the value here.

This module is the recognised-key registry plus the check that reads it. It
deliberately does **not** raise: an unknown key has always been accepted, and
turning that into an error would break every manifest already relying on it.

Scope
-----
Only the tables whose key sets can be stated with confidence are checked:
objects, their methods/properties/state/init-params, and module-level
functions. Handle, composer and capsule modules (``kind = "handle"`` and
friends in :mod:`._handle`) carry their own method vocabulary — ``returns``,
``out_len_fn``, ``caller_out`` — and are **skipped** rather than guessed at.
A false warning on a valid key is worse than the silence this replaces: it
trains the reader to ignore the channel. Widening the registry to those tables
is additive and safe to do later; warning on them now is not.
"""

from __future__ import annotations

from . import _report

# --- object tables ---------------------------------------------------------

#: Keys valid directly on a ``[<component>]`` table.
OBJECT_KEYS = frozenset(
    {
        "arg_type",
        "return_type",
        "class_name",
        "doc",
        "mutable",
        "no_state",
        "no_step",
        "no_reset",
        "opaque_state",
        "serializable",
        "streamable",
        "stream_block_default",
        "async_stream",
        "step_delegates_to_steps",
        "array_args",
        "create_fn",
        "create_error",
        "create_error_message",
        "init_post_parse",
        "depends_on",
        "extra_link_libs",
        "extra_include_dirs",
        # `jm apply --fragment` routes an object into a module with this
        # (`_apply.py`'s module_directives): the fragment cannot edit the
        # manifest's `[module.X] objects` list itself.
        "module",
        # The four lifecycle bodies a manifest may supply, each with its
        # `_file` companion — the pairs `_apply._validate_fragment_impl_keys`
        # enforces mutual exclusion over.
        "impl",
        "impl_file",
        "create_impl",
        "create_impl_file",
        "reset_impl",
        "reset_impl_file",
        "destroy_impl",
        "destroy_impl_file",
        "replace",
        # Sub-tables. They arrive as ordinary keys of the component dict, so
        # omitting them here would warn on every object that declares one.
        "state",
        "methods",
        "properties",
        "init_params",
        "views",
        "warnings",
        "destroy",
    }
)

#: Keys valid on a ``[[<component>.state]]`` entry. ``opaque`` (struct field
#: with no generated accessors), ``no_ctor`` (field, but not a constructor
#: parameter) and ``controllable`` (per-call ``steps()`` override) are the
#: three flags that change what the field becomes rather than what it is.
STATE_KEYS = frozenset(
    {
        "name",
        "type",
        "default",
        "doc",
        "opaque",
        "no_ctor",
        "controllable",
    }
)

#: Every key valid on an init-param, **in the order `_dump` writes them**,
#: paired with whether the value is a bare TOML boolean.
#:
#: Mirrors the fields :func:`_config.init_param_tuple_to_dict` persists and
#: :func:`_config._project_init_params` reads back — the pair that defines the
#: constructor's shape.
#:
#: gh-838: the order and the bool flag live here, with the names, rather than
#: in a second list beside the serializer. This registry already said `capsule`
#: and `header` were valid keys while `_config._dump` had no branch writing
#: them — so jm's own validator accepted a key the writer then dropped, and a
#: capsule init-param came back as a scalar of an unknown C type. A separate
#: ordered copy next to the emitter would have re-created exactly that gap one
#: key later, which is the shape of the bug it was added to fix.
INIT_PARAM_FIELDS: tuple[tuple[str, bool], ...] = (
    ("name", False),
    ("type", False),
    ("default", False),
    ("default_raw", False),
    ("real_type", False),
    ("real_create_fn", False),
    ("optional", True),
    ("create_fn", False),
    ("required", True),
    ("doc", False),
    ("capsule", False),
    ("header", False),
)

#: The same set, unordered, for key validation. Derived so it cannot disagree.
INIT_PARAM_KEYS = frozenset(key for key, _is_bool in INIT_PARAM_FIELDS)

#: Keys valid on a ``[[<component>.methods]]`` entry.
#:
#: The union of what the three manifest writers handle: ``_dump``'s explicit
#: block (``_config._KNOWN_METHOD_KEYS``), the keys ``_apply._replay_method``
#: forwards into ``_method.run``, and the flags ``_script`` re-emits. That is
#: the consumer contract — a key outside it reaches no writer and no renderer.
METHOD_KEYS = frozenset(
    {
        "name",
        "doc",
        "arg_type",
        "return_type",
        # gh-805 §A2: bind a C symbol that is not `<comp>_<name>`.
        "fn",
        # call shape
        "params",
        "extra_args",
        "varargs",
        "pass_capacity",
        "count_default",
        "batch",
        "nogil",
        # result shape
        "variable_output",
        "multi_output",
        "out_type",
        "out_divisor",
        "max_out",
        "max_results",
        "result_fields",
        "single",
        "record_name",
        "record_module",
        "record_doc",
        "record_dtype",
        "py_return_type",
        "none_on_empty",
        # error translation
        "status_return",
        "error_negative",
        "error",
        "error_message",
        # misc
        "manual_stub",
        "bench",
        "codec",
        "sink_fn",
        "impl",
        "impl_file",
        "replace",
    }
)

#: Keys valid on a ``[[<component>.methods.params]]`` entry.
PARAM_KEYS = frozenset(
    {
        "name",
        "type",
        "default",
        "enum",
        "out",
        "mutable",
        "doc",
        "capsule",
        "header",
        # gh-805 §C: array shape/interleave metadata. `rank` is the opt-in
        # `PyArray_NDIM` guard; `elements_per_sample` is the interleave factor
        # between numpy's element count and the samples the kernel counts.
        "rank",
        "elements_per_sample",
        # A codec method's params carry the tag/variant role (`_codec.py`).
        "role",
    }
)

#: Keys valid on a ``[[<component>.properties]]`` entry.
PROPERTY_KEYS = frozenset(
    {
        "name",
        "type",
        "ctype",
        "doc",
        "writable",
        "mutable",
        "field",
        "buf_field",
        "len_field",
        "valid_field",
        "count_field",
        "type_field",
        "value_field",
        "expr",
        "enum",
        "value_type",
        "entry_type",
        "count_fn",
        "key_fn",
        "value_fn",
        "entry_fn",
        "codec",
        "capsule",
        "default",
        "out",
    }
)

# --- module-level functions ------------------------------------------------

#: Keys valid on a ``[[module.<mod>.functions]]`` entry.
FUNCTION_KEYS = frozenset(
    {
        "name",
        "doc",
        "params",
        "return_type",
        "inline",
        "out_type",
        "out_size",
        "result_fields",
        "max_results",
        "max_results_param",
        "variable_output",
        # gh-816's subject: a `jm function` key, and only that.
        "check_return",
        "impl",
        "impl_file",
        "replace",
    }
)

#: Keys valid on a ``[[module.<mod>.functions.params]]`` entry.
#:
#: gh-805 §C: `rank` and `elements_per_sample` are here as well as in
#: `PARAM_KEYS` because `_render`'s function-param builder and
#: `_context/_parse`'s method-param builder are the documented peer pair that
#: emits array acquisition — they now share the emitter, so recognising the
#: key on only one side would accept it in the manifest and drop it in the C.
FUNCTION_PARAM_KEYS = frozenset(
    {
        "name",
        "type",
        "out",
        "mutable",
        "default",
        "enum",
        "doc",
        "rank",
        "elements_per_sample",
    }
)


#: Kind -> its recognised keys. The kind names are what a warning prints, so
#: they read as the thing the author wrote in the manifest.
KIND_KEYS: dict[str, frozenset] = {
    "object": OBJECT_KEYS,
    "state": STATE_KEYS,
    "init_param": INIT_PARAM_KEYS,
    "method": METHOD_KEYS,
    "param": PARAM_KEYS,
    "property": PROPERTY_KEYS,
    "function": FUNCTION_KEYS,
    "function param": FUNCTION_PARAM_KEYS,
}


#: ``(kind, key)`` -> the advice that follows "is a <other> key". Only for
#: confusions where jm can name the thing the author actually wanted; a key
#: that is merely misplaced gets the generic message instead.
HINTS: dict[tuple[str, str], str] = {
    # gh-816. `check_return` raises when a `jm function` returns non-zero;
    # `status_return` is the method spelling of the same intent.
    ("method", "check_return"): (
        "on a method, `status_return = true` translates a non-zero return "
        "into an exception"
    ),
    # gh-805 §G's first instance: TOML binds a key written below
    # `[[<obj>.init_params]]` into that param table, where jm never looks for
    # it. The constructor's error translation is an object-level key.
    ("init_param", "create_error"): (
        "declare it on the `[<object>]` table — an init_param has no error "
        "translation of its own"
    ),
    ("init_param", "create_error_message"): (
        "declare it on the `[<object>]` table, beside `create_error`"
    ),
}


class Unknown:
    """One unrecognised key, and everything needed to explain it.

    Attributes
    ----------
    kind : str
        The table's kind, e.g. ``"method"``.
    where : str
        Human-readable location, e.g. ``"meter.set_truth"``.
    key : str
        The offending key as written.
    valid_for : tuple of str
        Other kinds whose vocabulary *does* contain `key`, in a stable order.
        Empty when the key belongs to no table jm knows.
    """

    __slots__ = ("kind", "where", "key", "valid_for")

    def __init__(self, kind: str, where: str, key: str, valid_for: tuple):
        self.kind = kind
        self.where = where
        self.key = key
        self.valid_for = valid_for

    def message(self) -> str:
        """Render the one-line warning body (no ``warning:`` prefix)."""
        head = f"{self.where}: unknown {self.kind} key `{self.key}`"
        hint = HINTS.get((self.kind, self.key), "")
        if self.valid_for:
            kinds = " or ".join(f"{k}" for k in self.valid_for)
            article = "an" if self.valid_for[0][0] in "aeiou" else "a"
            head += f" — it is {article} {kinds} key"
        if hint:
            head += f"; {hint}"
        elif self.valid_for:
            head += (
                f"; jm does not read it on a {self.kind}, so it has no effect"
            )
        else:
            head += " — jm does not read it anywhere, so it has no effect"
        return head

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Unknown({self.kind!r}, {self.where!r}, {self.key!r})"


def _check(kind: str, where: str, table: dict) -> list:
    """Collect the unrecognised keys of one table.

    Keys beginning with ``_`` are transient render state (``_doc_blocks``) and
    are never manifest-authored, so they are skipped exactly as ``_dump``
    skips them.
    """
    valid = KIND_KEYS[kind]
    out = []
    for key in table:
        if key.startswith("_") or key in valid:
            continue
        valid_for = tuple(
            k for k, keys in KIND_KEYS.items() if k != kind and key in keys
        )
        out.append(Unknown(kind, where, key, valid_for))
    return out


def _entries(table: dict, key: str) -> list:
    """Return ``table[key]`` when it is a list of dicts, else ``[]``."""
    value = table.get(key)
    if not isinstance(value, list):
        return []
    return [e for e in value if isinstance(e, dict)]


def unknown_keys(cfg: dict) -> list:
    """Find every unrecognised key in a merged manifest.

    Parameters
    ----------
    cfg : dict
        A manifest as :func:`_config.load` returns it — fragments already
        merged in, so a split-layout project is covered by the same walk.

    Returns
    -------
    list of Unknown
        In manifest order: objects, then each object's sub-tables, then
        module functions. Empty for every manifest jm itself writes, which is
        what makes this safe to run on every load.

    Notes
    -----
    Tables jm cannot characterise are skipped rather than guessed at — see
    this module's docstring. In particular a ``kind``-bearing module (handle,
    composer, capsule) is left alone entirely.
    """
    # `components` is the canonical answer to "which top-level tables are
    # objects" — it already excludes `app`, `enum` and `codec`, which a
    # hand-rolled reserved-name list here silently missed, reporting every
    # `[app]` key as an unknown object key.
    from ._config import components

    found: list = []
    for name in components(cfg):
        section = cfg.get(name)
        if not isinstance(section, dict):
            continue
        found += _check("object", name, section)
        for entry in _entries(section, "state"):
            found += _check("state", f"{name}.{entry.get('name', '?')}", entry)
        for entry in _entries(section, "init_params"):
            found += _check(
                "init_param", f"{name}.{entry.get('name', '?')}", entry
            )
        for entry in _entries(section, "properties"):
            found += _check(
                "property", f"{name}.{entry.get('name', '?')}", entry
            )
        for entry in _entries(section, "methods"):
            where = f"{name}.{entry.get('name', '?')}"
            found += _check("method", where, entry)
            for p in _entries(entry, "params") + _entries(entry, "extra_args"):
                found += _check("param", f"{where}({p.get('name', '?')})", p)
    for mod, data in (cfg.get("module") or {}).items():
        if not isinstance(data, dict) or data.get("kind"):
            # A handle/composer/capsule module has its own vocabulary.
            continue
        for entry in _entries(data, "functions"):
            where = f"{mod}.{entry.get('name', '?')}"
            found += _check("function", where, entry)
            for p in _entries(entry, "params"):
                found += _check(
                    "function param", f"{where}({p.get('name', '?')})", p
                )
    return found


#: Warnings already emitted this process, so a command that loads the manifest
#: several times (``jm apply`` loads the real tree and its temp scaffold) says
#: each thing once.
_SEEN: set = set()


def warn_unknown_keys(cfg: dict, stream=None) -> list:
    """Print one ``warning:`` line per unrecognised key, deduplicated.

    Returns the messages emitted (for tests). Emits nothing — and costs one
    dict walk — for a manifest jm generated, which is the common case.
    """
    out = []
    for unknown in unknown_keys(cfg):
        text = unknown.message()
        if text in _SEEN:
            continue
        _SEEN.add(text)
        _report.warn(text, gates=False, stream=stream)
        out.append(text)
    return out

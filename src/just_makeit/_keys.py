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
        "process_global",
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
        # gh-999: `[[<obj>.init_groups]]` — one row instantiates a `[[group]]`
        # under a prefix, so a struct member repeated N times costs one row
        # instead of N copies of its param list.
        "init_groups",
        "views",
        "warnings",
        "destroy",
    }
)

#: Keys valid on a ``[[<component>.init_groups]]`` row (gh-999). Two, because
#: a group instantiation says only WHICH group and under WHAT prefix — every
#: other property of the params it produces belongs to the group's own field
#: declarations, where it is written once.
INIT_GROUP_KEYS = frozenset({"group", "prefix"})

#: Keys valid on a top-level ``[[group]]`` table (gh-999).
GROUP_KEYS = frozenset({"name", "fields"})


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
    # gh-900: pass this array's length as a NAMED scalar argument
    # placed immediately before its data pointer, rather than as the
    # trailing `<name>_len` jm emits by default.
    #
    # gh-1097: a LIST names a 2-D array's two extents instead
    # (`derived = ["ny", "nx"]`), which jm otherwise declares as
    # `<name>_dim0`/`<name>_dim1`. The value is polymorphic by design and the
    # writers branch on its type, not on the key — a second key would have
    # made "which one wins" a question with no good answer.
    ("derived", False),
    # gh-1096: the C type this parameter is DECLARED with, when that is a
    # typedef jm has no vocabulary for — an enum typedef, in every case seen
    # so far. jm still passes an int and C converts at the call, so this
    # changes the injected declaration and nothing else. Restricted to
    # integer-rendered params for that reason; see `_state._ctor_c_type`.
    ("c_type", False),
    # gh-1105: a value jm may use when it needs to CONSTRUCT one of these for
    # a generated smoke test or doctest. Not a default — the parameter stays
    # required and the Python signature is unchanged. It exists because a
    # validating constructor rejects the type's zero, which is the only value
    # jm can invent, so `_unseedable_required` suppressed the construction and
    # the whole generated suite skipped.
    ("example_value", False),
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
        # gh-805 §D: trust `max_out` as a true bound; drop the
        # `max(max_out, n)` clamp without changing the kernel signature.
        "exact_max_out",
        "count_default",
        "count_name",
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

#: The keys on a method entry that describe its CALL SIGNATURE — everything a
#: caller or the generated binding can observe, as opposed to how it reads.
#:
#: Derived by subtraction so it cannot fall behind ``METHOD_KEYS``: a new key
#: is a signature key unless it is explicitly listed as prose or provenance
#: below. Getting that default backwards is what gh-1011 cost — a view method
#: silently kept its parent's ``arg_type`` because nothing compared the two.
#:
#: ``fn`` is deliberately NOT here. It names the C symbol rather than the
#: signature, and on a view override it is the *declaration* that a separate
#: symbol exists to carry a separate signature — so treating it as part of the
#: comparison would make every signature override differ from itself.
METHOD_NON_SIGNATURE_KEYS = frozenset(
    {
        "name",
        "doc",
        "fn",
        # prose about the result, not its shape
        "record_doc",
        # provenance: where the body came from, not what it looks like
        "impl",
        "impl_file",
        "replace",
        "bench",
    }
)

#: Keys whose value must match for two method entries to be the same call.
METHOD_SIGNATURE_KEYS = METHOD_KEYS - METHOD_NON_SIGNATURE_KEYS

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
# --- `kind`-bearing module tables (gh-1114) ---------------------------------
#
# A handle / capsule / composer module was checked by NOTHING: `unknown_keys`
# skipped it outright, so a key from the wrong face and an outright typo both
# reported clean and both did nothing. That silence is what made gh-1111 hard
# to see from outside -- three keys written, one honoured, no warning.
#
# **These vocabularies were validated by running, not by reading.** Assembling
# them from the code alone is not reliable: `error_message` is valid on a
# handle method and is read in `_context/_diagnostics`, so it escapes any grep
# of the three generator files, and the docs' own key tables are partial. What
# makes them trustworthy is that the checker runs over doppler's real manifest,
# every bundled example and the whole test suite with zero findings -- a gate
# keeps that true.
#
# The per-KIND split is load-bearing rather than tidy: a capsule or composer
# method legitimately spells its signature `arg_type` / `return_type`, and a
# handle method spells it `args` / `returns`. One shared set would have to
# accept all four and would then miss the most likely mistake on either face.

_SHARED_MODULE_KEYS = frozenset(
    {
        "kind",
        "backing",
        "package",
        "header",
        "depends_on",
        "extra_link_libs",
        "extra_include_dirs",
        "extra_types",
        "doc",
        "no_generate",
        "reexports",
        "capsule_name",
        "functions",
        "functions_in_core",
        "serializable",
        "optional_backend",
        "init_params",
        "methods",
        "properties",
        "enums",
    }
)

HANDLE_MODULE_KEYS = _SHARED_MODULE_KEYS | {
    "handle_type",
    "type_name",
    "create_fn",
    "init_fn",
    "create_args",
    "create_post",
    "create_error",
    "create_error_message",
    "close_fn",
    "close_returns",
    "context_manager",
    "getters",
    "factories",
    "capsule",
}
CAPSULE_MODULE_KEYS = _SHARED_MODULE_KEYS | {
    "create_fn",
    "destroy_fn",
    "create_error",
    "create_error_message",
    "type_name",
    "getters",
}
COMPOSER_MODULE_KEYS = _SHARED_MODULE_KEYS | {
    "composer",
    "composes",
    "sample_type",
    "source",
    "segment",
    "timeline",
    "oo",
    "json",
    "cli",
    "serializers",
    "getters",
    "type_name",
    "create_fn",
    "stream",
    "generator",
    "flat_sources",
}

#: A handle method: `args` / `returns`, and the gh-565/gh-1111 status raise.
HANDLE_METHOD_KEYS = frozenset(
    {
        "name",
        "fn",
        "returns",
        "nogil",
        "args",
        "error",
        "error_message",
        "out_len_fn",
        "doc",
    }
)
#: A capsule / composer method: the OBJECT spelling of a signature.
CC_METHOD_KEYS = frozenset(
    {
        "name",
        "fn",
        "arg_type",
        "return_type",
        "caller_out",
        "nogil",
        "doc",
        "args",
        "returns",
    }
)
KIND_GETTER_KEYS = frozenset({"fn", "out", "cache", "fields", "doc"})
KIND_GETTER_FIELD_KEYS = frozenset(
    {
        "name",
        "from",
        "type",
        "enum",
        "scale",
        "expr",
        "getter",
        "writable_fn",
        "writable",
        "doc",
        # gh-1137. NOT optional decoration: `_handle` REFUSES a field
        # that has an `expr` and no `returns` (gh-333), because
        # `returns` types the `tmp` the expr operates on -- defaulting
        # it to the decoded `type` truncates the getter's value before
        # the expr runs, silently. Omitting it here produced two
        # messages from one manifest, each saying the opposite of the
        # other, and the warning invited the author to delete the key
        # that makes their project stop compiling.
        "returns",
    }
)
KIND_FACTORY_KEYS = frozenset({"name", "create_fn", "init_params", "doc"})
KIND_CREATE_ARG_KEYS = frozenset(
    {"name", "type", "enum", "default", "kwonly", "capsule", "header", "doc"}
)
KIND_CREATE_POST_KEYS = frozenset({"fn", "when", "arg"})
KIND_METHOD_ARG_KEYS = frozenset(
    {"name", "type", "default", "writable", "enum", "capsule", "kwonly"}
)
KIND_DEPENDS_ON_KEYS = frozenset({"name", "link", "test_only"})
KIND_PROPERTY_KEYS = frozenset(
    {"name", "type", "writable", "doc", "enum", "getter", "setter", "fn"}
)
KIND_SERIALIZER_KEYS = frozenset(
    {"name", "fn", "header", "params", "returns", "doc"}
)
KIND_INIT_PARAM_KEYS = frozenset(
    {"name", "type", "default", "enum", "capsule", "header", "doc", "kwonly"}
)

#: (kind, sub-table) -> the `KIND_KEYS` entry that validates its rows. A table
#: absent from here is not walked, which is why `_kind_tables` reports one it
#: does not know rather than passing it silently -- an unwalked table is the
#: state this whole issue is about.
KIND_TABLE_VOCAB = {
    ("handle", "methods"): "handle method",
    ("capsule", "methods"): "capsule method",
    ("composer", "methods"): "composer method",
    ("handle", "getters"): "kind getter",
    ("capsule", "getters"): "kind getter",
    ("composer", "getters"): "kind getter",
    ("handle", "factories"): "kind factory",
    ("handle", "create_args"): "kind create_arg",
    ("handle", "create_post"): "kind create_post",
    ("handle", "properties"): "kind property",
    ("capsule", "properties"): "kind property",
    ("composer", "properties"): "kind property",
    ("handle", "init_params"): "kind init_param",
    ("capsule", "init_params"): "kind init_param",
    ("composer", "init_params"): "kind init_param",
    ("composer", "serializers"): "kind serializer",
    # A `kind`-bearing module may also carry module-level free functions,
    # and they are the SAME shape as a plain module's -- so they reuse the
    # object face's vocabulary rather than getting a near-copy. Found by
    # running the checker over the suite: without this the table had no
    # vocabulary and was reported as an unknown key on two real projects.
    ("handle", "functions"): "function",
    ("capsule", "functions"): "function",
    ("composer", "functions"): "function",
    ("handle", "depends_on"): "kind depends_on",
    ("capsule", "depends_on"): "kind depends_on",
    ("composer", "depends_on"): "kind depends_on",
}


KIND_KEYS: dict[str, frozenset] = {
    "object": OBJECT_KEYS,
    "state": STATE_KEYS,
    "init_param": INIT_PARAM_KEYS,
    "init_group": INIT_GROUP_KEYS,
    "method": METHOD_KEYS,
    "param": PARAM_KEYS,
    "property": PROPERTY_KEYS,
    "function": FUNCTION_KEYS,
    "function param": FUNCTION_PARAM_KEYS,
    # gh-1114: the `kind`-bearing module faces. Registered here so `_check`
    # and `Unknown.valid_for` treat them exactly like every other table --
    # a key written on the wrong face is then named as belonging to the
    # right one, which is the whole point.
    "handle module": HANDLE_MODULE_KEYS,
    "capsule module": CAPSULE_MODULE_KEYS,
    "composer module": COMPOSER_MODULE_KEYS,
    "handle method": HANDLE_METHOD_KEYS,
    "capsule method": CC_METHOD_KEYS,
    "composer method": CC_METHOD_KEYS,
    "kind getter": KIND_GETTER_KEYS,
    "kind getter field": KIND_GETTER_FIELD_KEYS,
    "kind factory": KIND_FACTORY_KEYS,
    "kind create_arg": KIND_CREATE_ARG_KEYS,
    "kind create_post": KIND_CREATE_POST_KEYS,
    "kind method arg": KIND_METHOD_ARG_KEYS,
    "kind depends_on": KIND_DEPENDS_ON_KEYS,
    "kind property": KIND_PROPERTY_KEYS,
    "kind serializer": KIND_SERIALIZER_KEYS,
    "kind init_param": KIND_INIT_PARAM_KEYS,
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
    # gh-1114. The confusions the two faces invite, each naming the spelling
    # that works here. `status_return` is the one gh-1111 was reported with:
    # written beside a working `error`, it read as if it did something.
    ("handle method", "status_return"): (
        'this face spells it `error = "<Exception>"` over an `int` '
        "`returns` — a non-zero return then raises"
    ),
    ("handle method", "error_negative"): (
        "a handle method has one status shape: `error` over an `int` "
        "`returns`, which treats any non-zero return as the failure"
    ),
    ("handle method", "arg_type"): (
        "a handle method declares its arguments as `args = [{ name = ..., "
        "type = ... }]`"
    ),
    ("handle method", "return_type"): ("a handle method spells it `returns`"),
    ("capsule method", "args"): (
        "a capsule/composer method declares one `arg_type` / `return_type` "
        "pair, not an argument list"
    ),
    ("composer method", "args"): (
        "a capsule/composer method declares one `arg_type` / `return_type` "
        "pair, not an argument list"
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
        elif self.kind == "unwalked sub-table":
            head = (
                f"{self.where}: sub-table `{self.key}` has no key vocabulary,"
                " so nothing inside it is checked (gh-1114)"
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


def _check_kind_module(mod: str, data: dict) -> list:
    """Collect the unrecognised keys of one ``kind``-bearing module (gh-1114).

    Sub-tables are walked only when their rows are TABLES. ``composes`` and
    ``extra_link_libs`` are arrays of plain strings, and treating any list as
    a sub-table reported both of doppler's as unknown tables -- found by
    running this over doppler rather than over a manifest jm wrote itself,
    which is the same way the guard in `_procglobal` was found.

    A sub-table jm has no vocabulary for is reported rather than skipped. An
    unwalked table is precisely the state this issue is about, so it must not
    be reachable by simply adding one.
    """
    kind = str(data.get("kind"))
    if f"{kind} module" not in KIND_KEYS:
        return []  # not a kind jm generates; nothing to say about it
    found = _check(f"{kind} module", mod, data)
    for tbl, rows in data.items():
        if not isinstance(rows, list) or not any(
            isinstance(r, dict) for r in rows
        ):
            continue
        vocab = KIND_TABLE_VOCAB.get((kind, tbl))
        if vocab is None:
            # NOT "unknown key" -- the key may be perfectly valid; what is
            # missing is a vocabulary for its ROWS, so everything inside it
            # is unchecked. That is the state this whole issue is about, so
            # it is reported rather than skipped: adding a sub-table must not
            # be a way back into the silence.
            found.append(Unknown("unwalked sub-table", mod, tbl, ()))
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            where = f"{mod}.{tbl}[{row.get('name', row.get('fn', '?'))}]"
            found += _check(vocab, where, row)
            # Nested inline-table arrays: a method's `args`, a getter's
            # `fields`. They carry the shapes most likely to be typo'd.
            for nested, nvocab in (
                ("args", "kind method arg"),
                ("fields", "kind getter field"),
                ("init_params", "kind init_param"),
                ("params", "function param"),
            ):
                for item in row.get(nested, []) or []:
                    if isinstance(item, dict):
                        found += _check(nvocab, f"{where}.{nested}", item)
    return found


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
        for entry in _entries(section, "init_groups"):
            found += _check(
                "init_group", f"{name}.{entry.get('group', '?')}", entry
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
        if not isinstance(data, dict):
            continue
        if data.get("kind"):
            # gh-1114: it has its own vocabulary -- which is now written down,
            # so it is checked against that rather than skipped.
            found += _check_kind_module(mod, data)
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

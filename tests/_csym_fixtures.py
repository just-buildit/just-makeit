"""A broad fixture tree for gh-1591's symbol stem, built by running the tool.

One driver, two callers: ``tests/test_gh1633_stem_reaches_every_symbol.py``
builds it under a stem override to prove every derived C symbol is routed
through `_csym`, and the byte-identity check of a `_csym` refactor builds it
with two jm sources and diffs the trees.

Built by running jm, never hand-written: a hand-written fixture would be a
fixture of what someone thinks jm emits, which is the thing under test
(gh-1181's rule).
"""

from __future__ import annotations

from pathlib import Path

from _jmrun import run_cli

#: One project per row: its ``jm new`` arguments, then the CLI steps.
#: Between them every shape phase 2 prefixes: a standalone object with init
#: params, state, methods (plain, array, batch, varargs, borrow, record,
#: single), properties (plain, writable, field), a streamable object, the
#: stateless and step-less shapes, perf, a module with objects, a method,
#: a property, a view and functions, and a dotted module.
PROJECTS: "dict[str, tuple[tuple[str, ...], list[tuple[str, ...]]]]" = {
    "std": (
        ("std",),
        [
            (
                "object",
                "fir",
                "--state",
                "gain:double:1.0",
                "--init-param",
                "n:int:4",
            ),
            (
                "method",
                "fir",
                "scale",
                "--arg-type",
                "double",
                "--return-type",
                "double",
            ),
            (
                "method",
                "fir",
                "fill",
                "--arg-type",
                "float[]",
                "--return-type",
                "void",
            ),
            ("property", "fir", "level", "--type", "double"),
            ("property", "fir", "bias", "--type", "double", "--writable"),
            ("object", "osc", "--arg-type", "void", "--return-type", "float"),
            ("object", "blob", "--no-state"),
            ("object", "sink", "--no-step"),
            ("object", "src", "--streamable"),
            ("object", "named", "--class-name", "Renamed"),
        ],
    ),
    "perf": (
        ("pf", "--perf"),
        [
            ("object", "acc", "--state", "k:double:2.0"),
        ],
    ),
    "shapes": (
        ("sh",),
        [
            ("object", "ser", "--state", "k:double:1.0", "--serializable"),
            ("object", "hdr", "--header-only"),
            ("object", "nr", "--no-reset", "--mutable"),
            ("object", "opq", "--no-step", "--opaque-state"),
            (
                "method",
                "ser",
                "burst",
                "--arg-type",
                "float[]",
                "--return-type",
                "float",
                "--variable-output",
            ),
            (
                "method",
                "ser",
                "peaks",
                "--arg-type",
                "float[]",
                "--return-type",
                "peak_t",
                "--result-field",
                "idx:int",
                "--result-field",
                "val:float",
            ),
            (
                "method",
                "ser",
                "best",
                "--arg-type",
                "float[]",
                "--return-type",
                "best_t",
                "--result-field",
                "idx:int",
                "--single",
            ),
            (
                "method",
                "ser",
                "tune",
                "--arg-type",
                "double",
                "--return-type",
                "double",
                "--fn",
                "sh_ser_tune_impl",
            ),
            ("method", "ser", "call", "--varargs"),
        ],
    ),
    "kinds": (
        ("kd",),
        [
            ("object", "clip", "--state", "gain:double:1.0"),
            ("module", "own"),
            ("object", "flag", "--module", "own", "--state", "n:int:0"),
        ],
    ),
    "mod": (
        ("md",),
        [
            ("module", "m"),
            ("object", "o", "--module", "m", "--state", "g:double:1.0"),
            (
                "method",
                "o",
                "gain2",
                "--module",
                "m",
                "--arg-type",
                "double",
                "--return-type",
                "double",
            ),
            ("property", "o", "lvl", "--module", "m", "--type", "double"),
            ("view", "o", "Peek", "--module", "m", "--create-fn", "o_open"),
            ("function", "calc", "--module", "m"),
            ("module", "dsp.filters"),
            ("object", "bq", "--module", "dsp.filters"),
        ],
    ),
}


#: Manifest tables a row needs that no CLI verb writes: the `kind` modules
#: and `process_global`. Merged into the manifest after the CLI steps, then
#: `apply` renders them -- the path a hand-edited manifest takes.
MANIFEST: "dict[str, dict]" = {
    "kinds": {
        "flag": {"process_global": "true"},
        "module": {
            "playlist": {
                "kind": "composer",
                "backing": "playlist",
                "composes": ["clip"],
                "source": {
                    "object": "clip",
                    "struct": "clip_t",
                    "type_name": "Clip",
                    "fields": [
                        {"name": "gain", "type": "double", "default": "1.0"}
                    ],
                    "generates": {
                        "generator": "clip",
                        "bridge_fn": "clip_from_source",
                    },
                    "computed": [
                        {"name": "dur", "type": "double", "fn": "clip_dur"}
                    ],
                },
                "segment": {
                    "type_name": "Track",
                    "struct": "track_t",
                    "sources": "multi",
                    "fields": [{"name": "n", "type": "size_t"}],
                },
                "oo": {"composer_type_name": "Mix"},
            },
            "hand": {
                "kind": "handle",
                "backing": "b",
                "header": "kd/b.h",
                "type_name": "H",
                "close_fn": "b_close",
                "create_fn": "b_open",
                "create_args": [],
                "depends_on": [{"name": "flag", "link": True}],
            },
        },
    },
}


def _merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v


def build(where: Path) -> "dict[str, Path]":
    """Scaffold every project in :data:`PROJECTS` under *where*, then
    ``apply`` each, and return ``{row: project root}``.

    Every step must succeed: a fixture that half-built would make a clean
    oracle mean nothing.
    """
    roots = {}
    for row, (new_args, steps) in PROJECTS.items():
        r = run_cli("new", *new_args, cwd=where)
        assert r.returncode == 0, f"{row}: new: {r.stdout}{r.stderr}"
        root = where / new_args[0]
        for step in steps:
            r = run_cli(*step, cwd=root)
            assert r.returncode == 0, f"{row}: {step}: {r.stdout}{r.stderr}"
        if row in MANIFEST:
            from just_makeit import _config as C

            cfg = C.load(root)
            _merge(cfg, MANIFEST[row])
            C.save(root, cfg)
        r = run_cli("apply", cwd=root)
        assert r.returncode == 0, f"{row}: apply: {r.stdout}{r.stderr}"
        roots[row] = root
    return roots


def tree(root: Path) -> "dict[str, bytes]":
    """Every file under *root* by POSIX-relative path, build outputs aside."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }

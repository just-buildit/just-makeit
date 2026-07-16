"""_error.py — ``just-makeit error`` command (gh-482).

Declares how a component's ``create()`` failure reports to Python.

``create()`` returning NULL is the only failure channel C has, so it carries
every reason a component can refuse to construct: out of memory, yes — but
also an invalid parameter combination, an unsatisfiable constraint, a
physically impossible configuration. jm reported all of them as
``MemoryError``, which for anything but an allocation failure is simply false:
a caller passing bad parameters got ``MemoryError: acq_create returned NULL``
when the truth was a ``ValueError``. That is misleading in a traceback and
uncatchable the way a caller would naturally reach for it.

jm already gets this right for everything it validates itself — enum choices
and array shapes raise ``ValueError``. It was only the C-side refusal, the one
case where the *component* knows why it failed, that got flattened.

This is a translation fix, not a new hook: C can already signal failure, jm was
just mistranslating it on arrival. Pure glue — no sacred file is touched.

Known limit, inherent to the design: NULL is NULL. With ``create_error``
declared, *every* create() failure reports as that category, including a
genuine allocation failure. Distinguishing reasons needs an err out-param on
``create()``, which changes the C API in the sacred ``_core.h``/``_core.c`` and
requires the component to set the code itself — deliberately out of scope; see
the ``gh-482-errors-wip`` branch.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import _config as C
from . import _glue


def run(
    root: Path,
    object_name: str,
    category: str,
    message: str,
    *,
    module: str | None = None,
) -> None:
    """Declare `object_name`'s create()-failure translation.

    Parameters
    ----------
    root : Path
        Project root (the directory holding ``just-makeit.toml``).
    object_name : str
        Component whose ``create()`` failure is being translated.
    category : str
        A name from `C.ERROR_CATEGORIES`, e.g. ``ValueError``.
    message : str
        Text for the raised exception.
    module : str, optional
        Owning module, for a module object.

    Notes
    -----
    Re-running replaces the previous declaration rather than accumulating one:
    there is a single failure channel, so there is a single translation.
    """
    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\n"
            "Run 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    if category not in C.ERROR_CATEGORIES:
        supported = ", ".join(sorted(C.ERROR_CATEGORIES))
        print(
            f"error: unsupported --category '{category}'.\n"
            f"Supported: {supported}",
            file=sys.stderr,
        )
        sys.exit(1)

    if not message:
        print(
            "error: --message is required — an exception with no text is no"
            " better than the MemoryError it replaces.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = C.load(root)

    all_comps = C.components(cfg)
    if module:
        if object_name not in C.module_objects(cfg, module):
            print(
                f"error: object '{object_name}' not found in module"
                f" '{module}'.",
                file=sys.stderr,
            )
            sys.exit(1)
    elif object_name not in all_comps:
        print(
            f"error: object '{object_name}' not found. Available: {all_comps}",
            file=sys.stderr,
        )
        sys.exit(1)

    previous = C.create_error(cfg, object_name)
    C.set_create_error(cfg, object_name, category, message)
    C.save(root, cfg)

    verb = "replacing" if previous else "adding"
    print(
        f"just-makeit: {verb} create() error translation on '{object_name}'"
        f" -> {category}" + (f" in module '{module}'" if module else "")
    )
    if previous and previous != category:
        print(f"  (was {previous})")
    print()
    print(f"  update  {cfg_path}")

    _glue.regenerate(root, cfg, object_name, module, C.project_name(cfg))

    if category != "MemoryError":
        print()
        print(
            f"Note: every {object_name}_create() failure now reports as"
            f" {category},\n      including a genuine allocation failure."
            " create() returning NULL\n      cannot distinguish them."
        )

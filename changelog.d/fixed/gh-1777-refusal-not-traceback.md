- **A manifest refusal prints one `error:` line, not a traceback**
    (gh-1777). An object declared both in `objects/<obj>.toml` and in the
    central manifest was refused with a good message -- the file, what is
    wrong, what to do -- under a dozen stack frames, by every command that
    reads the manifest (`apply`, `status`, `script`, `upgrade`, `method`,
    ...), so an author's own mistake read as a crash in jm. The same held
    under `jm upgrade` for a composer's partial owned-pointer set (gh-1711)
    and for two prototypes of one seam function (gh-1739). Deliberate
    refusals now raise `Refusal`, a `ValueError` subclass, and the CLI
    prints one as `error: <message>` and exits 1, as before. Only that type
    is caught, so a `ValueError` jm did not raise on purpose -- a bug --
    still tracebacks. `JM_DEBUG=1` brings a refusal's traceback back.
    Converted here: the fragment-merge refusals in manifest load, the
    composer owned-pointer and seam-prototype refusals, and the existing
    `[codec.X]` and `process_global` refusal types. The remaining bare
    `raise ValueError` sites are counted by a ratchet that only lets the
    count shrink (gh-1783).

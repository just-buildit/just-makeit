- **An owned pointer's reader can name its refusal** (gh-1735). An
    owned-pointer composer source field declared `parse_why = true` has the
    reason-naming `parse_fn`, `T *parse_fn(const char *, const char **why)`,
    declared so in `<module>_bridge.h`. Every face that reads its text form
    passes the reason through: the constructor keyword and the setter raise
    `ValueError(<the reason>)` instead of `<name>: <parse_fn> refused the text`, the generic `from_json` / `from_file` raise it instead of
    `invalid composer spec`, and the c-face CLI prints
    `bad --<name> TEXT: <reason>`. A refusal that writes no reason keeps the
    old message, and a field without the key renders byte-identically. Like
    gh-1722's switches, a `parse_why` that is not `true`/`false` is refused
    at load.

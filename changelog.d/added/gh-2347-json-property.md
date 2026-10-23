- **A property can hand Python a structured value parsed from JSON**
    (gh-2347). `type = "json"` on `[[<obj>.properties]]` declares a getter
    `char *<stem>_get_<name>(const <stem>_state_t *)` that returns malloc'd
    JSON text. The binding parses it with `json.loads` and frees it. `NULL`
    raises `RuntimeError`, malformed text raises `ValueError` naming the
    property, and `py_type` (for example `list[dict[str, Any]]`) is the stub
    annotation. For a core that already holds a structured document, such as
    doppler's SigMF annotations (doppler-dsp/doppler#2565).

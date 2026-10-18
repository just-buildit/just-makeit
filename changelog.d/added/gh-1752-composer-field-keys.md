- **A project declares the keys its own generator reads off composer field
    rows** (gh-1752). `[project] composer_field_keys = ["cli", "json", ...]`
    names them once; jm keeps them on a save as before and stops warning, on
    every load, that each one "has no effect". Only on `source.fields` /
    `segment.fields` rows, and only the names listed: any other unknown key
    there still warns, so a misspelt jm key is no longer buried in the
    expected ones. A listed name jm itself reads on a field row is refused.

- **A `bit_pattern` field refuses an element that is not 0 or 1, on every
    face (gh-2696).** A composer source field with `bytes = true` and
    `coerce = "bit_pattern"` copied a `bytes` value as it was but mapped an
    int sequence's elements to `v != 0`, so `bytes([8])` stored 8 and `[8]`
    stored 1, and `from_json` stored any element up to 255. Every spelling
    -- `bytes`, `bytearray`, a list, a numpy array, the setter, a
    project-read field's non-`str` value and `from_json` -- now refuses a
    non-bit element with one `ValueError` naming the field and the element,
    and pointing at `np.unpackbits` for octets. A plain `bytes` field is
    unchanged.

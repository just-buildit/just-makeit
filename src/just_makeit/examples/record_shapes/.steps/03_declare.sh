cd evlog

# ── shape 1: ONE record, by value ───────────────────────────────────────────
just-makeit method collector summary \
    --arg-type void \
    --return-type evlog_summary_t \
    --single \
    --result-field "n:uint64_t" \
    --result-field "mean:double" \
    --record-name Summary \
    --record-doc "Count and mean of everything recorded so far."

# ── shape 2: an ARRAY of records, as a structured ndarray ────────────────────
just-makeit method collector read \
    --arg-type void \
    --return-type double \
    --variable-output \
    --record-dtype evlog_rec_t \
    --result-field "t:uint64_t" \
    --result-field "v:double"

# ── shape 3: a list of tuples ───────────────────────────────────────────────
just-makeit method collector peaks \
    --arg-type void \
    --return-type evlog_peak_t \
    --result-field "index:size_t" \
    --result-field "value:double"

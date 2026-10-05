just-makeit new budget
cd budget

just-makeit object allocator \
    --init-param "capacity:size_t:required" \
    --init-param "slots:size_t:required" \
    --state "n_slots:size_t:0" \
    --state "remaining:size_t:0" \
    --state "degraded:bool:false" \
    --arg-type size_t \
    --return-type size_t

# Seed valid constructor arguments for jm's generated tests (gh-1105).
# `example_value` is TOML-only, and the generated tests are create-only, so
# regenerate the component to write them with it.
python3 - <<'EOF'
from pathlib import Path
p = Path("objects/allocator.toml")
s = p.read_text(encoding="utf-8")
for name, value in (("capacity", "1024"), ("slots", "4")):
    old = f'name = "{name}"\ntype = "size_t"\nrequired = true\n'
    assert old in s, name
    s = s.replace(old, old + f'example_value = "{value}"\n', 1)
p.write_text(s, encoding="utf-8")
EOF
just-makeit regenerate allocator --force

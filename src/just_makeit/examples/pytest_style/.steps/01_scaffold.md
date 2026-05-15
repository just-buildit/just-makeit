## 1. Scaffold with both flags

```{01_scaffold.sh}
```

Both flags land in `[project]` in `just-makeit.toml`:

```toml
[project]
name = "dsp_algo"
version = "0.1.0"
pytest = "true"
pytest_benchmark = "true"
```

Every subsequent `just-makeit object` call reads these flags and generates the
matching test/bench style automatically — no need to repeat the flags per object.

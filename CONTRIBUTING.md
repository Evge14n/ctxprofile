# Contributing

Thanks for looking at ctxprofile. It's small, offline, and has no runtime
dependencies — keep it that way.

## Setup

```
python -m venv .venv
. .venv/Scripts/activate      # or source .venv/bin/activate
pip install -e ".[dev]"
```

## The bar

Every change keeps all three green:

```
ruff check .
mypy
pytest -q
```

- No runtime dependencies (standard library only). Dev tools are fine.
- Tests run offline, from committed fixtures — no live API calls in CI.
- Label a number by its confidence; don't present an estimate as exact.

## Style

Conventional commit subjects (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`).
Small, focused pull requests.

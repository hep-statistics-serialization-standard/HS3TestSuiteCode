# HS3TestSuite

Backend-neutral HS3 conformance fixtures and checks.

The committed fixtures are frozen HS3 JSON files with per-test expected results.

Run the suite with:

```bash
python -m hs3suite run --backend roofit
```

Validate the test suite with:

```bash
pytest
```

# Errors

Error codes: one `@contracterror` enum, one failure mode per method.

```python
--8<-- "examples/errors.py"
```

Build: `stellar serpent build examples/errors.py`. Tests:
`tests/unit/test_examples.py` (tier 1 vs the mini host) and
`tests/real_host/test_examples_real.py` (tier 1 vs the real host).

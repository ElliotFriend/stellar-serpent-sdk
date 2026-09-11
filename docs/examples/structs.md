# Structs

Structs: a `@contracttype` record, stored under a `@contracttype` KEY.

```python
--8<-- "examples/structs.py"
```

Build: `stellar serpent build examples/structs.py`. Tests:
`tests/unit/test_examples.py` (tier 1 vs the mini host) and
`tests/real_host/test_examples_real.py` (tier 1 vs the real host).

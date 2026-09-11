# Shapes

Tagged unions and int enums: one value that is one of several shapes.

```python
--8<-- "examples/shapes.py"
```

Build: `stellar serpent build examples/shapes.py`. Tests:
`tests/unit/test_examples.py` (tier 1 vs the mini host) and
`tests/real_host/test_examples_real.py` (tier 1 vs the real host). It is
also a testnet deployment (see [Deployments](../deployments.md)).

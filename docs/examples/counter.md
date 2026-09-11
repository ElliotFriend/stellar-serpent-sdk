# Counter

The smallest contract that can have a state bug: a counter with a ceiling.

```python
--8<-- "examples/counter.py"
```

Build: `stellar serpent build examples/counter.py`. Tests:
`tests/unit/test_examples.py` (tier 1 vs the mini host) and
`tests/real_host/test_examples_real.py` (tier 1 vs the real host).

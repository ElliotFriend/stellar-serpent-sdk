# Events

Events: both publish spellings, a topics-marked event, and an all-data one.

```python
--8<-- "examples/events.py"
```

Build: `stellar serpent build examples/events.py`. Tests:
`tests/unit/test_examples.py` (tier 1 vs the mini host) and
`tests/real_host/test_examples_real.py` (tier 1 vs the real host).

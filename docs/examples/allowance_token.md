# Allowance token

An allowance-style token, without cross-contract calls (spec S6).

```python
--8<-- "examples/allowance_token.py"
```

!!! note "`from_` renders as `from_`"
    Python reserves `from`, so the `Transfer` event's field is `from_`. The
    field name is emitted verbatim into the spec, so generated bindings show
    `from_` where a Rust contract would show `from`. Aliasing is an M2 item.

Build: `stellar serpent build examples/allowance_token.py`. Tests:
`tests/unit/test_examples.py` (tier 1 vs the mini host) and
`tests/real_host/test_examples_real.py` (tier 1 vs the real host).

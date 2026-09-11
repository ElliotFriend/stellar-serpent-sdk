# Bounty board

One contract that touches every M1 authoring surface: a constructor, struct
keys, a tagged union with a unit, an `Address`, and a `U32` payload, an int
enum, an error enum, three event data formats, temporary storage with an
explicit TTL, and `require_auth` on the poster, the worker, and the stored
admin. It is the M1 showcase deployment (see [Deployments](../deployments.md)).

```python
--8<-- "examples/bounty_board.py"
```

Build: `stellar serpent build examples/bounty_board.py`. Tests:
`tests/unit/test_examples.py` (tier 1 vs the mini host) and
`tests/real_host/test_example_bounty_board_real.py` (tier 1 vs the real host).

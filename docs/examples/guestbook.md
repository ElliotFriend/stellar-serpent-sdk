# Guestbook

A port of the [Ye Olde Guestbook](https://github.com/ElliotFriend/ye-olde-guestbook)
demo contract: an admin-owned guestbook whose constructor requires the admin's
authorization and writes the welcome message, `write_message` and
`edit_message` each requiring the acting author's authorization, a
`@contracttype` message record stored under a `@contractunion` key, and a
`@contracterror` enum for the four failure modes. It is the example written
the way a Soroban developer coming from Rust would write it, with Google-style
docstrings on every method.

```python
--8<-- "examples/guestbook.py"
```

Build: `stellar serpent build examples/guestbook.py`. Tests:
`tests/unit/test_example_guestbook.py` (tier 1) and
`tests/real_host/test_example_guestbook_real.py` (the real host), one test per
test in the original Rust contract's `test.rs`.

# Examples

Seven contracts, each rendered here straight from its source under
`examples/`: read the code on this site and you are reading exactly what
`stellar serpent build` compiles and what the test suite runs.

| Name | What it shows | Declared protocol |
|---|---|---|
| [Counter](counter.md) | the smallest contract that can have a state bug: a counter with a ceiling | 20 |
| [Errors](errors.md) | error codes: one `@contracterror` enum, one failure mode per method | 22 |
| [Structs](structs.md) | a `@contracttype` record, stored under a `@contracttype` key | 20 |
| [Events](events.md) | both publish spellings, a topics-marked event, and an all-data one | 20 |
| [Allowance token](allowance_token.md) | an allowance-style token, without cross-contract calls | 22 |
| [Shapes](shapes.md) | tagged unions and int enums: one value that is one of several shapes | 20 |
| [Bounty board](bounty_board.md) | one contract that touches every M1 authoring surface | 22 |

The declared protocol is the computed floor, not a per-example setting: an
example with an `__init__` compiles to a `__constructor`, a capability the
host only honors from protocol 22 (spec S13, CAP-0058), so `errors`,
`allowance_token`, and `bounty_board` declare 22 while the rest declare the
base import floor, 20. `stellar serpent inspect` shows the same number as the
floor it recomputes from a built artifact's own imports.

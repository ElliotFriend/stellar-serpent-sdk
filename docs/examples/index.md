# Examples

Seven contracts, each rendered here straight from its source under
`examples/`: read the code on this site and you are reading exactly what
`stellar serpent build` compiles and what the test suite runs.

| Name | What it shows |
|---|---|
| [Counter](counter.md) | the smallest contract that can have a state bug: a counter with a ceiling |
| [Errors](errors.md) | error codes: one `@contracterror` enum, one failure mode per method |
| [Structs](structs.md) | a `@contracttype` record, stored under a `@contracttype` key |
| [Events](events.md) | both publish spellings, a topics-marked event, and an all-data one |
| [Allowance token](allowance_token.md) | an allowance-style token, without cross-contract calls |
| [Shapes](shapes.md) | tagged unions and int enums: one value that is one of several shapes |
| [Bounty board](bounty_board.md) | one contract that touches every M1 authoring surface |

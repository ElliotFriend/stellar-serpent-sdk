# serpent — Decision Log

Judgment calls made autonomously during M1 execution, recorded for review and
reversal. Each entry: context, decision, why, and reversal cost. Fine-grained
task rulings live in the per-plan SDD ledgers during execution; entries here are
the ones with lasting consequence. (Decisions made *with* Elliot in-session are
in the spec/findings docs, not here.)

Format:

```
## YYYY-MM-DD <short title>
- Context:
- Decision:
- Why:
- Reversal cost:
```

## 2026-08-26 Standing-autonomy mechanics for M1
- Context: Elliot granted continue-through-M1 autonomy with a reviewable
  decision record ("keep chugging... clearly see where those decisions were
  made so we can reverse/change them").
- Decision: This log is that record. Sub-plans B–G are authored, adversarially
  reviewed, triaged, executed, and locally merged to main without per-phase
  sign-off; every judgment call lands here in the same commit series as the
  work. Hard stops remain: irreversible/outward actions and the spec-mandated
  user-approved testnet deployment at M1's end.
- Why: Matches the granted balance of momentum vs. auditability.
- Reversal cost: None — process-level; revoke anytime.

## 2026-08-26 M1-A review adoptions that shape user-facing surface
- Context: M1-A adversarial review; all findings adopted (triage presented
  in-session). Three shape the authoring surface beyond the session summary:
  `errorcode(N)` declarations, `Bytes32`/`Bytes64` aliases (both spec-corrected,
  flagged to Elliot), and `Vec(U32)`/`Map(Symbol, U32)` explicit element types
  at construction with `Vec[U32]`/`Map[K, V]` as annotation-only forms.
- Decision: as stated; also reflected ops supported so `sum()` works over chain
  ints, and `**`/`divmod`/bitwise are explicit TypeErrors until a contract
  needs them; U256/I256 deferred to M2.
- Why: Each verified against mypy repros / runtime-generics limits; details in
  the M1-A plan's Global Constraints and the review transcript summary.
- Reversal cost: Low before sub-plan C consumes the surfaces; moderate after
  (compiler frontend patterns would need updating).

## 2026-08-26 Chain-int truthiness: `bool(x)` is `value != 0`
- Context: Task 5 implementer escalated — `_ChainInt` had no `__bool__`, so
  `bool(U32(0))` was `True` (Python object default), making `if amount:` a trap.
- Decision: `__bool__` returns `value != 0` on every numeric chain type;
  semantics-table cases added in Task 10; the sub-plan C frontend must lower
  truthiness tests to the equivalent zero-comparison.
- Why: Matches Python intuition AND compiles exactly (i64.eqz); the TypeError
  alternative forces noisier code without a fidelity gain.
- Reversal cost: Low before sub-plan C; a compile-reject could replace it later
  at the cost of breaking `if amount:` in existing contracts.

## 2026-08-26 Timepoint/Duration: no arithmetic in M1-A
- Context: Task 5 review — both types inherited full _ChainInt arithmetic by
  default (Timepoint * Timepoint "worked"), while Rust's newtypes expose no ops.
- Decision: disable ALL arithmetic on Timepoint/Duration (TypeError naming the
  omission and pointing at the to_u64/from_u64 bridges). Deliberate time algebra
  (Duration+Duration, Timepoint-Timepoint→Duration) is a sub-plan E decision.
  Also: Python bool accepted wherever int is for numeric operands (bool ⊂ int is
  Python; the compiler tier rejects it statically anyway) — Task 10 table
  documents it; Bool's ordering accepts plain bool, matching its equality.
- Why: oracle fidelity to the Rust/host surface beats convenience acquired by
  inheritance; additive to re-enable later.
- Reversal cost: trivial (re-allow ops); reverse direction would break contracts.

## 2026-08-26 Bytes-family equality is payload-based
- Context: Task 6 — should Bytes32(p) == Bytes(p)?
- Decision: yes — equality/ordering/hash across Bytes/Bytes32/Bytes64 compare
  payloads (same _SCVAL_RANK). Fixed-length-ness is an authoring constraint;
  on-chain all three are the same BytesObject host type, and val_cmp answers 0
  for equal payloads.
- Why: type-exact equality would diverge from on-chain observable behavior.
- Reversal cost: one predicate + tests, before sub-plan C freezes patterns.

## 2026-08-26 No negative indexing on chain containers/buffers
- Context: Task 7 flagged Vec.get(-1) IndexError vs Bytes[-1] following Python.
- Decision: indexing is chain-faithful everywhere — negative indices raise
  IndexError on Vec.get AND Bytes.__getitem__ (aligned in Task 8). Slicing keeps
  Python semantics as authoring sugar (compiler tier will bound what compiles).
- Why: host vec_get/bytes_get take U32Vals; negative indices are unrepresentable
  on-chain, and oracle surfaces must not answer questions the chain cannot.
- Reversal cost: two lines + tests.

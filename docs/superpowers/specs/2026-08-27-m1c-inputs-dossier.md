# M1-C Design-Inputs Dossier

> Compiled by a research agent 2026-08-27; adopted by the controller with all §E recommendations ruled ADOPTED (see decisions.md entries of the same date). This document is the citation target for the M1-C plan.

# M1-C DESIGN-INPUTS DOSSIER — Compiler Frontend (Python source → resolved, typed IR + diagnostics + `must_reject/`)

Compiled 2026-08-27. Sources read: spec §2/§4/§5/§7/§11/§13, M1 roadmap row C, `decisions.md` (all 10 entries), phase0-findings §3/§4/§6, M1-A + M1-B plan Global Constraints and per-task interfaces, `spikes/spike1/frontend.py` (543 lines, 16-node IR), the whole shipped `src/serpent` surface, `tests/semantics/cases.py` + `test_semantics.py`, `tests/fixtures/token_style.py`, `spikes/spike1/contract_src.py`, `.github/workflows/ci.yml`, `pyproject.toml`.

Absolute paths are used throughout. Repo root: `/Users/elliotvoris/Dev/stellar/sdk/py-soroban`.

---

## A. FROZEN INPUTS

Every ruling and banked item that constrains the frontend. The plan should cite these by ID.

### A.1 Spec obligations (`docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md`)

| ID | Constraint | Source |
|---|---|---|
| S1 | serpent is a Python-*shaped* language: the subset is a specification and the compiler **rejects everything outside it with source-located errors rather than approximating**. | §1 Non-goals |
| S2 | M1 chain-type set is closed: `Bool U32 I32 U64 I64 U128 I128 Symbol Bytes Bytes32/Bytes64/bytes_n(N) String Vec[T] Map[K,V] Address Timepoint Duration Void`. `U256/I256/MuxedAddress/Val` are later. | §2 bullet 1 |
| S3 | Plain `int`/`str`/`bool` literals coerce to the annotated chain type **with compile-time bounds checks**; unbounded `int` arithmetic is rejected. | §2 bullet 2 |
| S4 | Exported signatures require annotations; **docstrings flow into `contractspecv0` doc fields — verified wiring, not aspiration**. | §2 bullet 3 |
| S5 | Contract methods take `self` first; the compiler ignores it. `@contracttype` uses `dataclass_transform`. Zero-plugin claim must hold under `mypy --strict`. | §2 bullet 4, amended per phase0 §4 |
| S6 | `__init__` compiles to `__constructor` (host-reserved, protocol ≥ 22); host **launders** constructor errors to `Context(InvalidAction)`; docs must say so prominently. | §2 bullet 5, §13 |
| S7 | `raise MyError.X` compiles to the code-preserving form: Error Val `(code << 32) \| 3`, delivered via `fail_with_error` (`x.5`) or an escalated return. **Never a bare `unreachable`.** | §2 bullet 6, §13 |
| S8 | **`Error` is never a returnable value.** The type system must not admit `Error` (or a `Val` known to hold one) as a return type. | §2 bullet 7 |
| S9 | UDT conventions: named-field struct → `Map<Symbol,V>`; tuple struct → `Vec<V>`; tagged union → `Vec` led by variant Symbol; int enum → `u32`; `@contracterror` → u32 under `SCE_CONTRACT`. | §2 bullet 8 |
| S10 | `@contracterror` members are **exception classes** declared `NAME = errorcode(N)`, never bare ints; the compiler reads the code from the `ast.Call`. | §2 bullet 8 amendments |
| S11 | Events: `@contractevent` classes emitted via `contract_event` (`x.1`); convention enforced that `topic[0]` is a short `Symbol` event name. | §2 bullet 9 |
| S12 | Single-underscore privates are fine (host reserves only `__`-prefix, call-time only). **Spec XDR limits validated at compile time with source-located errors: function/field names ≤ 30, type names ≤ 60, docs ≤ 1024, tuple arity ≤ 12.** | §2 bullet 10 |
| S13 | `X \| None` → `OPTION`; `tuple[...]` → `TUPLE`; **`Result` has no Python analogue — not exposed in M1** (functions return a value or raise). | §2 bullet 11 |
| S14 | The subset is defined by an **executable specification**: `tests/must_reject/*.py`, each file annotated with its expected source-located error; **the docs' "unsupported constructs" table is generated from that directory**. | §2 final ¶ |
| S15 | Named constructs users will inevitably try, each needing support or a *good* error: `for x in vec`, comprehensions, f-strings, `try/except`, closures, default/keyword args, `len()`, slicing. "This diagnostics long tail is a first-class, ongoing work item, not a footnote." | §2 final ¶ |
| S16 | Pipeline: `source → ast.parse → frontend (imports, name resolution, type check; **every error carries file:line:col**) → typed IR → emitter`. | §4 |
| S17 | Validate inside the compiler; the spike's invalid-WASM classes (**early returns, missing returns, >9-char symbols**) "must be structurally impossible". | §4 emitter discipline |
| S18 | Declared protocol is **computed, never hand-set**; importing a function gated above the build target is a **source-located compile error naming the function**. | §4 |
| S19 | Linear memory is required in M1: `Symbol` > 9 chars, string/bytes literals, logging, bulk Vec/Map construction. Contracts needing no memory still compile memoryless. | §5 |
| S20 | Checked arithmetic (M6): explicit overflow checks routed to `fail_with_error` with a distinct `ArithmeticOverflow` code. | §4 |
| S21 | One Val codec (`serpent/val.py`); semantics tests run **the same table of cases** against the Python classes and compiled WASM, asserting identical results. | §10 |
| S22 | `SymbolSmall` ≤ 9 chars, 6 bits/char, high-order-first; charset `_`=1, `0-9`=2…, `A-Z`=12…, `a-z`=38…; `SCSYMBOL_LIMIT` = 32. | §13 |
| S23 | All exports ≤ 32 params and ≤ 32 results; entry points return exactly one i64. Import symbol names ≤ 10 chars. | §13 |
| S24 | Instance storage is not a durability: a sub-map in the instance entry, one shared TTL. | §13 |
| S25 | Footprint violations are `Storage(ExceededLimit)` and **non-recoverable** (uncatchable via `try_call`) — relevant to why `try/except` cannot exist. | §13 |

### A.2 Roadmap standing constraints (`docs/superpowers/plans/2026-08-26-m1-roadmap.md`)

| ID | Constraint | Source |
|---|---|---|
| R1 | C produces exactly: name resolution + import handling, typed IR, type checker, diagnostics engine (source-located, `must_reject/` executable subset spec seeded from Phase 0 + **the ~25 constructs users will try**), `Env`/storage/auth/events API **surface** recognition. Sole consumer: D. | line 20 |
| R2 | D consumes C's IR and must be able to do operand-stack validation, memory/data layout, scratch discipline, ABI prologues, overflow→`fail_with_error` from it. | line 21 |
| R3 | Standing constraints verbatim: single Val codec; validate-inside-compiler; **error codes never lost to `unreachable`**; `self`-first methods; exception-class errors; pre-validate at every nominally-fallible boundary; balance checks at `ret()`; pinned toolchain versions with drift-detection tests; adversarial review before execution; SDD with task-scoped reviews. | lines 26–32 |
| R4 | A→D sequential: C cannot defer to D anything D needs at IR-consumption time. | lines 13–14 |
| R5 | `spikes/` is frozen read-only reference until D supersedes the emitter — **C must never import or modify `spikes/**`**; port values by copy. | lines 34–36; m1a Global Constraints |

### A.3 Decision-log rulings (`docs/superpowers/decisions.md`) — all bind the frontend

| ID | Ruling | Source |
|---|---|---|
| D1 | Sub-plans B–G are authored, adversarially reviewed, executed and merged without per-phase sign-off; every judgment call lands in `decisions.md` in the same commit series. Hard stops: irreversible/outward actions, and the user-approved testnet deploy at M1's end. | lines 19–29 |
| D2 | `errorcode(N)` declarations; `Bytes32`/`Bytes64` aliases (no `BytesN[32]` subscript); **`Vec(U32)`/`Map(Symbol, U32)` explicit element types at construction with `Vec[U32]`/`Map[K,V]` as annotation-only forms**; reflected ops so `sum()` works; `**`/`divmod`/bitwise are explicit `TypeError`s until a contract needs them; U256/I256 → M2. Reversal cost is "moderate after C consumes the surfaces" — **C freezes these**. | lines 31–43 |
| D3 | **Chain-int truthiness: `bool(x)` is `value != 0`; the sub-plan C frontend must lower truthiness tests to the equivalent zero-comparison** (`i64.eqz`). | lines 45–54 |
| D4 | Timepoint/Duration have **no arithmetic at all** (TypeError naming the omission, pointing at `to_u64`/`from_u64`); time algebra is a sub-plan E decision. Python `bool` is accepted wherever `int` is at tier 1, **"the compiler tier rejects it statically anyway."** | lines 56–67 |
| D5 | Bytes-family equality/ordering/hash is **payload-based** across `Bytes`/`Bytes32`/`Bytes64` (same `_SCVAL_RANK`); fixed-length-ness is authoring-only. "Reversal cost: … before sub-plan C freezes patterns." | lines 69–76 |
| D6 | **No negative indexing** on chain containers/buffers (IndexError on `Vec.get` and `Bytes.__getitem__`); slicing keeps Python semantics as authoring sugar and **"the compiler tier will bound what compiles."** | lines 78–85 |
| D7 | **Storage keys are any chain value, not Symbol-only** (chain types + `@contracttype` instances). Also: `ledger().timestamp() -> U64`, `has() -> Bool`, **`@contract` rejects static/classmethods**. | lines 87–95 |
| D8 | Events inherit the `serpent.Event` base (a decorator cannot add statically visible members); `@contractevent` validates the base. **Event topics are a heterogeneous chain-value tuple, not `Vec[Symbol]`** — canonical `(Symbol, Address, Address)`. | lines 97–106 |
| D9 | `stellar-sdk` is a runtime dep of `serpent.spec` **only**; core (`val/types/errors/decorators/env`) stays zero-dep, enforced by `tests/unit/test_core_zero_dep.py`. "Authored contracts never need the extra; **building them does**" → C may depend on `serpent[spec]`. | lines 108–115 |
| D10 | **Spec type/case names restricted to the Symbol charset** `[a-zA-Z0-9_]` (stricter than XDR `string<60>`) because ecosystem tools render type names as Rust identifiers. Tightening later breaks contracts — C enforces the same charset. | lines 117–124 |

### A.4 M1-A banked items (`docs/superpowers/plans/2026-08-26-m1a-value-layer.md`)

| ID | Constraint | Source |
|---|---|---|
| A1 | `mypy --strict` covers **`src` AND `tests`** (`[tool.mypy] files = ["src","tests"]`); `tests/` subdirs are packages with `__init__.py`. | Global Constraints |
| A2 | `serpent/val.py` is THE codec; C imports it, never reimplements Val encoding. | Global Constraints; spec §10 |
| A3 | Small-value bounds: `MAX_SMALL_U64 = 2**56-1`; signed small iff `-(2**55) <= v <= 2**55-1`. C needs these to decide immediate-vs-host-object at compile time. | Global Constraints |
| A4 | **Checked-arithmetic contract, binding**: `+ - * // %`, unary `-`, reflected and augmented forms; any out-of-range result → `ArithmeticOverflow`, never wraps/widens; `//` truncates toward zero, `%` takes dividend's sign; `MIN % -1 == 0` (not a trap); `//0`/`%0` → `ZeroDivisionError`. | Global Constraints |
| A5 | `**`, `divmod`, bitwise: `TypeError` naming the omission — **"revisit when a contract needs them"** (open). | Global Constraints |
| A6 | Coercion rule C must reproduce statically: in-range plain `int` operands coerce either side; out-of-range → `ValueError`; foreign chain types → `TypeError`. | Global Constraints |
| A7 | Comparison contract: `__eq__` **never raises**, returns `False` for foreign/out-of-range; ordering is mathematical against any int; foreign chain types → `TypeError`. | Global Constraints |
| A8 | `_SCVAL_RANK` uses **`ScValType` order, not tag order**: Bool 0, Void 1, Error 2, U32 3, I32 4, U64 5, I64 6, Timepoint 7, Duration 8, U128 9, I128 10, U256 11, I256 12, Bytes 13, String 14, Symbol 15, Vec 16, Map 17, Address 18. Any C-side folding of comparisons must use this table. | Global Constraints |
| A9 | Reserved runtime error codes: `RESERVED_CODE_MIN = 0xFFFF_FF00`; `CODE_BAD_ARGUMENT = 0xFFFF_FFFF`; `CODE_ARITHMETIC_OVERFLOW = 0xFFFF_FFFE`. User codes ≥ `RESERVED_CODE_MIN` rejected. **254 of 256 reserved codes are unallocated — C owns the registry.** | Global Constraints; `src/serpent/errors.py:11-14` |
| A10 | Exception mapping rule: host traps ↔ builtins (`ZeroDivisionError`/`IndexError`/`KeyError`); contract errors ↔ `ContractError`; **authoring-time misuse ↔ `ValueError`/`TypeError`** — that third category is exactly what C turns into compile errors. Equality exempt. | Global Constraints |
| A11 | Do not "correct" `val.py` toward the frozen spike (`spikes/spike1/harness.py` has a stale object upper bound). | Global Constraints |
| A12 | `to_val()` raises `NotImplementedError("host object form; sub-plan B")` for object-form values — C cannot use tier-1 `to_val()` as a lowering oracle for `Bytes`/`String`/wide `Symbol`/wide ints/containers/`Address`. | Task 5/6/8 Interfaces |
| A13 | Runtime-generics limitation: element type passed **explicitly at construction** because `Generic` cannot deliver `T` at runtime (`__orig_class__` is set only after `__init__`). C's checker reads annotations; the runtime reads constructor args. | Task 7 Interfaces; `src/serpent/types/containers.py:5-12` |
| A14 | Map iteration/`keys()`/`values()`/positional accessors follow `val_cmp` order — **observable on-chain**; C must not reorder. | Task 7 Interfaces |
| A15 | `val_cmp` is an explicitly **partial** model of `obj_cmp`, "differential-validated in sub-plans D/F; extending the supported set requires extending the differential tests." | `src/serpent/types/_ordering.py:11-12` |
| A16 | **`bytes_n(n)` for arbitrary lengths "awaits compiler support in sub-plan C."** | `src/serpent/types/buffers.py:188` |
| A17 | Timepoint arithmetic rejection "is what the **compiler tier will do too**." | `src/serpent/types/numeric.py:298` |
| A18 | Containers are "the oracle the compiler will be proven against." | `src/serpent/types/containers.py:6` |
| A19 | `_serpent_type_` is the metadata dict "**sub-plan C reads**"; it is never read via `getattr` in typed code. | `src/serpent/decorators.py:5,68` |
| A20 | `errorcode(N)` "gives sub-plan C an unambiguous `ast.Call` to read the code from." | `src/serpent/decorators.py:97` |
| A21 | **`@contracttype` field mutation is invisible to mypy on the 3.11 floor (`frozen_default` is 3.12+, no runtime deps) — "sub-plan C rejects field assignment at compile time."** | `src/serpent/decorators.py:30-36` |
| A22 | The exact public namespace C resolves names against is `serpent.__all__` (pinned by `tests/unit/test_public_api.py`); `U256`/`I256`/`BytesN` deliberately absent. | `src/serpent/__init__.py:59-89` |
| A23 | `tests/fixtures/token_style.py` is the executable proof of the zero-plugin `--strict` claim — C must keep it compiling. | Task 10 Interfaces |
| A24 | `SemCase.source` is deliberately a **string, not a Callable**, "because a callable is opaque to a compiler" — it must be compilable by C/D in a method body. | Task 10 Interfaces; `tests/semantics/cases.py:6-9` |

### A.5 M1-B banked items (`docs/superpowers/plans/2026-08-27-m1b-host-interface.md`, `src/serpent/_host`, `src/serpent/spec`)

| ID | Constraint | Source |
|---|---|---|
| B1 | "**Sub-plan C reads the bindings to recognize API calls**"; the `_host` layer is data + pure functions only. | plan lines 11–18 |
| B2 | **Bindings are looked up BY NAME; export codes are data, never hardcoded.** C must never inline `l.1`, `x.5`, etc. | plan line 35; `_model.index_functions_by_name` |
| B3 | Per-arg Val-typedness and wasm types come from the **explicit exhaustive table** (`HostFn.val_typed_args`, `val_typed_ret`, `wasm_params`, `wasm_result`) — consumers must not re-derive them from `ret_type`. 19 pinned fns return a raw scalar. | `_model.py:83-107` |
| B4 | Protocol floor: `declared_protocol(fn_names, requested)` is **THE value D writes into `build_env_meta`**; `is None` check, never truthiness; `DEFAULT_TARGET_PROTOCOL = 27` is only the gate ceiling; below-floor `requested` → `ValueError`. **C must produce the used-function-name set that feeds this.** | `_protocol.py:70-89`; plan 240–255 |
| B5 | `ProtocolGateError` names **every** offending fn with its min/max protocol — the diagnostic shape C surfaces. | `_protocol.py:47-67` |
| B6 | `STORAGE_TYPE = {"temporary":0, "persistent":1, "instance":2}`; `CONTRACT_TTL_EXTENSION = {"instance_and_code":0,"instance":1,"code":2}` — sourced from rs-soroban-env v28.0.2; **"if unsourceable, defer the constant with a named TODO; never let sub-plan D invent it."** | `_scalars.py`; plan 104–107 |
| B7 | `to_spec_type` raises `SpecTypeError` for unmappables; **C's type checker must pre-empt every one of these with a better, source-located error**: `None`/`NoneType` (void is an empty `outputs`, not a `VOID` typedef), `Env` (dropped, not mapped), `Event`/`@contractevent` (deferred to E), `@contracterror` (an entry, not a type), `@contract` classes, `U256`/`I256`/`MuxedAddress`/`Val`/`Result`/`Tuple`, bare `Vec`/`Map` without element types, non-`X|None` unions, plain `int`/`str`/`bytes`/`bool`, and any other parameterized generic. | `src/serpent/spec/typemap.py:23-51,132-176,194-214` |
| B8 | `_SCALARS` matches **by exact class, not `issubclass`** — an author subclass of `U32` is unmappable. `Bytes` keys off `_LENGTH`, never a class whitelist. | `typemap.py:90-95,179-191` |
| B9 | **`build_spec_entries(contract_cls, *, types=...)`: `types` is not discovered, it is declared. "Sub-plan D collects the module's decorated classes and passes them here; a caller that omits `types` silently emits a spec whose UDT references have no matching entries."** → C must produce the module-level decorated-type inventory. | `src/serpent/spec/sections.py:134-159` |
| B10 | Entry order pinned: UDT structs (in `types` order), error enums, then functions with `__constructor` first then declaration order. | `sections.py:145-151` |
| B11 | `__init__` **is** emitted as `__constructor` even with no args (the CLI derives deploy-time `--arg-name` flags from it); **the decorators skip `__init__`'s name check and check no parameter names at all — sections closes that hole, and C must too.** | `sections.py:34-37,79-82,226-231` |
| B12 | Docstrings → doc fields as full cleandoc'd UTF-8 text (not first-line); > 1024 bytes → `SpecDocError` naming the declaration. serpent pre-validates for a **source-located** error. | `sections.py:17-31`; plan 325–330 |
| B13 | **Per-field and per-input docs are `b""`: `_serpent_type_` records no per-field doc. "A real gap, noted for sub-plan C."** Class and method docstrings ARE emitted. | `sections.py:43-46`; `tests/unit/test_sections.py:506-508` |
| B14 | `SC_SPEC_ENTRY_EVENT_V0` is **deferred to sub-plan E** — `SCSpecEventV0` needs a `data_format` and a per-parameter topic/data `location`, which `@contractevent` metadata does not carry. Emitting a guess "would ship a valid-but-lying spec." | `sections.py:38-42,203-210` |
| B15 | Name caps and where they live: function/input/field ≤ 30 (`decorators.NAME_LIMIT`), type names ≤ 60 (`TYPE_NAME_LIMIT`), error-case names ≤ 60 (`CASE_NAME_LIMIT`), docs ≤ 1024 (`DOC_LIMIT`). Length is checked **before** charset so an over-long name isn't misreported as a charset problem. | `sections.py:70-77`; `decorators.py:371-386` |
| B16 | Tuple spec types are deferred — **no authoring surface** → compile-reject. | plan lines 47–50 |
| B17 | `env_meta(27)` == `000000000000001b00000000` is **on-chain-verified**; the 64-byte counter spec golden is only **Rust-SDK-byte-compat**. Golden-attribution discipline carries into C. | plan lines 51–63; `tests/goldens/README.md` |
| B18 | HostFn invariants: export codes unique per module and sequential over base-63 `_0-9a-zA-Z`; `arity == len(arg_types) == len(arg_names)`; all-i64 at this pin is an **asserted invariant, not an assumption** (may change at re-pin). | plan 379–384; `_model.py:19-28` |

### A.6 Phase 0 frontend feeds (`docs/superpowers/specs/2026-08-26-phase0-findings.md` §6, `spikes/spike1/frontend.py`)

| ID | Item | Source |
|---|---|---|
| P1 | **Module-docstring skip**: the spike skips docstrings only at index 0 of *class* and *function* bodies (`frontend.py:485-487,530-532`), never at module level, so `tree.body[0]` being a docstring `ast.Expr` dies as "unsupported top-level statement: Expr" (`frontend.py:519-521`). M1 must skip it. | findings §6; `frontend.py:169-174` |
| P2 | **Synthetic error location**: `SpikeCompileError(f"expected exactly one @contract class…", 1, 0)` (`frontend.py:523-526`) invents line 1 col 0. M1 must never fabricate a location — use the real node span or an explicit whole-file location kind. | findings §6 |
| P3 | The spike did **no** general name resolution — it textually pattern-matched `env.storage().instance().set(...)` chains (`_method_chain`, `frontend.py:219-245`). The finding was "the designed surface parses cleanly," not a production frontend. | `frontend.py:1-10` |
| P4 | The spike accepted only **constant** arguments to `Symbol()`/`String()`/`U32()` (`_single_str_arg`/`_single_int_arg`, lines 193-208). `tests/fixtures/token_style.py:87` needs `Bool(who == admin)` — runtime-argument constructors are a required M1 generalization. |  |
| P5 | The spike rejected all top-level statements except imports and decorated classes (lines 519-521). `token_style.py:54-55` declares module-level `ADMIN = Symbol("ADMIN")` — **module-level chain constants are a required M1 generalization.** |  |
| P6 | A leaked operand passes `wasm-tools validate` (polymorphic `return`); the emitter's balance check lives at `ret()`. Corollary for C: **C must prove definite return on every path**, because validation cannot. | findings §3.5; spec §4 |
| P7 | `map_new_from_linear_memory` keys are `(u32 ptr, u32 len)` descriptor pairs sorted ascending as byte strings **at compile time**, not Vals; values ARE 8-byte Val words in relative handle space. The wrong layout validates then panics on-chain. C's IR must carry struct field order deterministically. | findings §3.1; `spikes/spike1/emitter.py:38-56` |
| P8 | No panic-free `&str → Symbol` path exists in soroban-sdk 27 → **pre-validate at every nominally-fallible boundary** (`SCSYMBOL_LIMIT` + `[a-zA-Z0-9_]`). Generalized standing constraint R3. | findings §3.2 |

### A.7 Semantics-table obligations (`tests/semantics/`)

| ID | Item | Source |
|---|---|---|
T1 | **All 20 `kind="reject"` cases are compile-reject obligations**: `U32(5)+2**32` (out-of-range int operand, ValueError), `U32(1)+U64(1)` and `I32(1)+I64(1)` (cross-width/cross-signedness), `U32(2)**U32(3)`, `divmod(U32(5),U32(2))`, `I32(1)&I32(1)`, `Bool(True)+U32(1)`, `Timepoint(5)+Duration(1)`, `Timepoint(1)+Timepoint(1)`, `-Duration(5)`, `Duration(3)*2`, `Vec(U32,[U32(1)]).push_back(Symbol("x"))`, `Symbol("")`, `Symbol("a"*33)`, `Bytes32(b"x")`, `Bytes64(b"x"*10)`, `Address("not-a-strkey")`. | `tests/semantics/cases.py:300-504` |
| T2 | The four `tier1_only=True` cases are the recorded compile-reject/undecided set: `bool_leaks_as_int_operand` (`U32(5) + True`), `symbol_does_not_coerce_from_str`, `bytes_does_not_coerce_from_raw_bytes`, `bytes_negative_index_traps`. | `cases.py:275-279, 378-391, 429-435` |
| T3 | **The three regexes in `tests/semantics/test_semantics.py:63-87` are explicitly a placeholder** ("deliberately crude — sub-plan C's real frontend checks are what will actually enforce these"): `_BOOL_AS_INT_OPERAND`, `_NEGATIVE_INDEX_LITERAL`, `_RAW_LITERAL_COMPARED_VIA_EQ`. **Replacing them with real frontend checks is a named C deliverable.** | `test_semantics.py:56-87` |
| T4 | **Open, assigned to C**: raw `str`/`bytes` operand coercion in `==` — "the tier-2 answer is undecided until sub-plan C settles raw-operand coercion." | `cases.py:376-377` |
| T5 | `symbol_underscore_vs_A_ascii_order` is flagged as "the top sub-plan D/F differential vector": tier 1 pins ASCII byte order (`Symbol("A") < Symbol("_")`), the host's 6-bit alphabet codes `_`=1 and `A`=12 — **if C lowers small-Symbol comparison to a raw packed-Val compare it gets the opposite answer.** | `cases.py:53-63, 415-425` |
| T6 | `"reject"` cases are tier-1-only **by construction** — D skips them because the contract never compiles. So C is the *only* tier that can prove them. | `cases.py:25-33` |

### A.8 Environment/CI constraints C inherits

| ID | Item | Source |
|---|---|---|
| E1 | CI gates: `ruff check .`, `ruff format --check src tests`, `mypy --strict` (over `src` and `tests`), `pytest -q` on py3.11/3.12/3.13. | `.github/workflows/ci.yml` |
| E2 | `testpaths = ["tests"]`; `[tool.mypy] files = ["src","tests"]` with **no excludes**; `[tool.ruff] src = ["src","tests","spikes"]`. | `pyproject.toml:31-46` |
| E3 | Non-test fixture modules under `tests/` are already an established pattern and ARE mypy-checked (`tests/fixtures/token_style.py`, `tests/unit/future_annotations_contract.py`, `no_future_annotations_contract.py`). | repo |
| E4 | PEP 563 (`from __future__ import annotations`) is a supported authoring form, asserted to produce byte-identical `_serpent_type_` metadata via `typing.get_type_hints`. | `tests/unit/test_decorators.py:504-506`; `decorators.py:302-313,389-404` |
| E5 | Python floor is 3.11 — `ast` gives `end_lineno`/`end_col_offset` (full spans available); `dataclass_transform(frozen_default=)` is not available. | `pyproject.toml`; `decorators.py:30-36` |

---

## B. THE SUBSET INVENTORY

Derived from spec §2's construct list (S15), the semantics table, the spike's 16-node IR, and an exhaustive sweep of Python 3.11 `ast` node kinds. "★" marks genuinely contestable lines needing a controller decision (cross-referenced in §E).

### B.1 Statements

| Construct | M1-C line | Lowering sketch / rejection message intent |
|---|---|---|
| `Module` | **SUPPORT** | Module docstring skipped (P1). Body limited to: `from __future__ import annotations`, `from serpent import …`, module-level chain constants, decorated classes, module-level private functions ★. |
| `Import` / `ImportFrom` | **SUPPORT (restricted)** | Only `from serpent import <names in serpent.__all__>` (A22) and `from __future__ import annotations` (E4). Every other import → reject naming the module: "a contract may only import from `serpent`". |
| `ClassDef` | **SUPPORT (4 decorators only)** | `@contract` (exactly one), `@contracttype`, `@contracterror`, `@contractevent`. Undecorated or multi-decorated class → reject (mirrors `decorators._reject_redecoration`). No base classes except `Event` on `@contractevent` (D8). |
| `FunctionDef` (contract method) | **SUPPORT** | → `FuncIR`. `self` first (S5, D7 rejects static/classmethod); all other params + return annotated; `__init__` → export `__constructor`, must be `-> None` (S6, B11). No defaults, `*args`, `**kwargs` (decorators.py:328-345, re-raised source-located). |
| `FunctionDef` (module-level private helper) | **SUPPORT ★** | Internal (non-exported) WASM function. Spec §2's own example calls a module-level `balance(env, from_)`. Recursion rejected via call-graph cycle check ★ (§E8). |
| `FunctionDef` (nested / closure) | **REJECT** | "nested functions and closures are not supported; contracts are a flat set of methods and module-level helpers". |
| `AsyncFunctionDef` / `AsyncFor` / `AsyncWith` / `Await` | **REJECT** | "there is no event loop on chain; contract methods are synchronous". |
| `Return` | **SUPPORT** | Real 0x0F. Anywhere in the body; C proves **definite return on every path** for non-void methods and rejects `return <expr>` in a `-> None` method (P6, S17). `Error`-typed return → reject (S8). |
| `Assign` (single `Name` target) | **SUPPORT** | `LetLocal`/`Assign`. Locals are single-typed: first binding fixes the type; rebinding at a different type → reject. |
| `Assign` (tuple/multi target, attribute target, subscript target) | **REJECT** | Tuple unpacking: "assign one name at a time". Attribute target: "`@contracttype` values are immutable; build a new one" (A21 — this is the mypy hole C closes). Subscript target: "use `Vec.put(i, v)` / `Map.set(k, v)`". |
| `AugAssign` (`+= -= *= //= %=`) | **SUPPORT ★** | Desugar to `x = x <op> y` before typing; only on locals of arithmetic chain types. `**=`/`&=`/`\|=`/`^=`/`<<=`/`>>=` → reject naming the omission (A5, D2). |
| `AnnAssign` in class body | **SUPPORT** | `@contracttype`/`@contractevent` field declaration. Annotation must pass the `to_spec_type` surface (B7). |
| `AnnAssign` in a function body (`x: U32 = e`) | **SUPPORT ★** | Explicit local type annotation; must agree with the inferred type. Bare `x: U32` with no value → reject (no uninitialized locals). |
| `If` / `elif` / `else` | **SUPPORT** | Structured `if`/`else` blocks; `elif` is nested `If`. Condition must be `Bool`-typed, a comparison, a numeric chain value (truthiness → zero-test, D3), or `not <bool>`. Truthiness of `Symbol`/`String`/`Bytes`/`Vec`/`Map`/`Address`/struct → **reject** (see §F.3). |
| `While` | **SUPPORT ★** | WASM `block`+`loop`+`br_if`. Same condition rules. Budget exhaustion is the author's problem (documented). |
| `Break` / `Continue` | **SUPPORT** | `br` to the enclosing block/loop label. Reject outside a loop. |
| `For x in vec` | **SUPPORT ★** (recommended) | Desugar **in C** to a `While` over `vec_len`/`vec_get` with a hidden `U32` induction local, so D never sees a `For`. `#1` construct users will try (S15). Fallback if descoped: reject with the exact while-loop rewrite in the `help:` line. |
| `For i in range(...)` | **SUPPORT ★** | `range(stop)` and `range(start, stop)` with chain-int/int-literal bounds, same desugaring. 3-arg/negative-step → reject in M1. |
| `For … in map` / `for … in bytes` / `for … in tuple` | **REJECT (M1)** | Point at `map.keys()` + `for k in keys`. |
| `For … else` | **REJECT** | "the `for … else` clause is not supported". |
| `Raise MyError.X` | **SUPPORT** | `fail_with_error(error_val(code))` (`x.5`). Code read from the `errorcode(N)` `ast.Call` (A20) or the imported metadata. **Never `unreachable`** (R3, S7). |
| `Raise` other forms (`raise X(...)`, bare `raise`, `raise … from …`) | **REJECT** | "only `raise <ErrorEnum>.<Member>` is supported; contract errors are u32 codes, not exception instances". |
| `Try` / `TryStar` / `except` / `finally` | **REJECT** | "a contract cannot catch its own errors: a failing frame is rolled back by the host, and footprint violations are non-recoverable (spec §13). Validate before acting." Cross-contract `try_call` is M2. |
| `With` | **REJECT** | "there is no context-manager protocol on chain". |
| `Match` | **REJECT (M1)** | "structural pattern matching is not supported"; note it as the natural future form for tagged unions (S9). |
| `Assert` | **REJECT** | "`assert` has no on-chain meaning; `raise <Error>.<Member>` to fail with a code the caller can read" (S7 — an assert lowered to `unreachable` is exactly the banned pattern). |
| `Delete` (`del x`) | **REJECT** | "use `storage.del_(key)` / `Vec.del_(i)` / `Map.del_(k)`". |
| `Global` / `Nonlocal` | **REJECT** | "contract state lives in storage; module-level names are compile-time constants". |
| `Pass` | **SUPPORT** | No-op. |
| `Expr` (docstring) | **SUPPORT** | Skipped at module, class and function level (P1); function/class docstrings flow to `contractspecv0` (S4, B12). |
| `Expr` (bare call statement) | **SUPPORT** | Void-returning host calls: storage `set`/`del_`/`extend_ttl`, `require_auth`, `events().publish`, container mutators. Any non-void expression discarded as a statement → reject (catches `count + U32(1)` on its own line). |

### B.2 Expressions

| Construct | M1-C line | Lowering sketch / rejection message intent |
|---|---|---|
| `Constant` (int/str/bytes/bool/None) | **SUPPORT (in typed position)** | Literals coerce to the annotated/inferred chain type with **compile-time bounds checks** (S3). A bare literal with no chain type in scope → reject: "wrap it in a chain type, e.g. `U32(5)`". |
| `Name` (param / local / module const / type / decorated class) | **SUPPORT** | `ParamRef`/`LocalRef`/`ConstRef`/`TypeRef`. Unresolved → reject naming it. `self` resolves but any *use* → reject: "contract state lives in storage, not on `self`". |
| `Attribute` — struct field read | **SUPPORT** | `symbol_new_from_linear_memory` (`b.j`) if the field name > 9 chars, else SymbolSmall immediate; then `map_get` (`m.1`). |
| `Attribute` — `ErrorEnum.Member` | **SUPPORT** | In a `raise` position only; elsewhere → reject (S8: `Error` is not a value). |
| `Attribute` — chain-type properties (`.value`, `.text`, `.data`, `.strkey`, `.is_account`, `.hi64`, `.lo64`, `.element_type`) | **REJECT (M1)** | No host equivalent; they are tier-1 introspection. Name the property and the alternative. |
| `Call` — chain-type constructor with a runtime arg (`U32(x)`, `Bool(a == b)`) | **SUPPORT** | Identity / representation change; range re-check where the source type is wider (P4). |
| `Call` — chain-type constructor with a literal | **SUPPORT** | `Symbol("…")` ≤ 9 chars → SymbolSmall immediate, > 9 → `symbol_new_from_linear_memory` (`b.j`) over a pooled literal (S19, S22). `String(…)` → `string_new_from_linear_memory` (`b.i`). `Bytes(b"…")` → `bytes_new_from_linear_memory` (`b.3`). |
| `Call` — `Vec(T)`, `Vec(T, [items])`, `Map(K,V)`, `Map(K,V,[(k,v)…])` | **SUPPORT** | D2/A13: element type is a **type** in a value position, and the items argument is a **list/tuple display** recognized *only* in that position. `Vec(T,[…])` → `vec_new_from_linear_memory` (`v.g`) for all-static items, else `vec_new` (`v._`) + `vec_push_back` (`v.6`) chain. `Map(…)` → `map_new_from_linear_memory` (`m.9`) with compile-time-sorted key descriptors (P7), else `map_new` (`m._`) + `map_put` (`m.0`). |
| `Call` — `@contracttype` construction, **kwargs only** | **SUPPORT** | `map_new_from_linear_memory` (`m.9`); field names sorted ascending as byte strings at compile time (P7). Positional args → reject (mirrors `frontend.py:304-309`). |
| `Call` — `bytes_n(N)` in an annotation | **SUPPORT ★** | A16's banked item. Requires either evaluating the call or a literal-N special case (§E1). |
| `Call` — `@contractevent` construction + `.publish(env)` | **SUPPORT ★ or REJECT** | Blocked on B14/D8: `_serpent_type_` carries no topic/data split. Either C defines the split or rejects with "deferred to sub-plan E" (§E12). |
| `Call` — `env.…` chains, `Address.require_auth*`, container methods | **SUPPORT** | See §C.4 for the full recognition table. |
| `Call` — `len(x)` | **SUPPORT ★** | `vec_len` (`v.3`) / `map_len` (`m.3`) / `bytes_len` (`b.8`) / `string_len` (`b.k`) / `symbol_len` (`b.l`), typed **`U32`** (tier 1 returns `int` — see §F.4). |
| `Call` — `bool(x)` | **SUPPORT** | D3: zero-test → `Bool`. |
| `Call` — `sum()`, `min()`, `max()`, `abs()`, `int()`, `str()`, `print()`, `isinstance()`, any other builtin | **REJECT** | Name the builtin. (`sum()` works at tier 1 only because reflected ops exist — D2 — but it needs an iterator protocol the compiler has no analogue for.) |
| `Call` — user-defined module-level helper / private method | **SUPPORT ★** | Internal call (§E8). |
| `BinOp` `+ - * // %` | **SUPPORT** | A4's contract exactly: overflow → `ArithmeticOverflow` via `fail_with_error`; `//` truncates toward zero; `%` takes dividend's sign; `MIN % -1 == 0`; `//0`/`%0` trap. i128/u128 route to the guest runtime library (spec §6). |
| `BinOp` `** @ & \| ^ << >>` | **REJECT** | A5/D2: message names the omission. |
| `BinOp` `/` (true divide) | **REJECT** | "there are no floats on chain; use `//` for truncating integer division". |
| `UnaryOp` `-` | **SUPPORT** | Overflow-checked (`-U32(1)` and `-I32(MIN)` are `ArithmeticOverflow`, per cases.py:165-199). |
| `UnaryOp` `not` | **SUPPORT** | Only on a `Bool`-typed / comparison operand → `Bool`. |
| `UnaryOp` `+` / `~` | **REJECT** | Unary `+` is a no-op nobody needs; `~` is bitwise (A5). |
| `Compare` — single op `== != < <= > >=` | **SUPPORT** | Immediates → `i64.eq`/`lt_s`… on the narrowed type; host-object types (`Address`, `Bytes`, `String`, `Symbol`, containers, structs) → `obj_cmp` (`x.0`, raw i64 return). **Small `Symbol` comparison must also route through `obj_cmp`, not a raw packed compare (T5).** |
| `Compare` — chained (`a < b < c`) | **REJECT ★** | "compare two values at a time" (recommended; desugaring is possible but the temporary-evaluation semantics are a trap). |
| `Compare` — `in` / `not in` / `is` / `is not` | **REJECT** | `in` → "use `Map.has(k)` / `Vec.first_index_of(v)`". `is` → "identity has no on-chain meaning; use `==`". |
| `Compare` — chain value vs raw `str`/`bytes` literal | **REJECT ★** (recommended) | T4's open question; recommending reject makes the three `tier1_only` coercion cases permanently tier-1-only and settles the undecided tier-2 answer. |
| `BoolOp` `and` / `or` | **SUPPORT ★ (Bool-only)** | Short-circuit lowering to nested `if`, result `Bool`. Restricted to `Bool`-typed/comparison operands, because Python's value-returning semantics (`U32(0) and U32(5)` → `U32(0)`) have no sound single-type lowering (§E9). |
| `IfExp` (`a if c else b`) | **SUPPORT ★** | `if`/`else` with both arms the same type. |
| `Subscript` — `Bytes[i]` | **SUPPORT** | `bytes_get` (`b.6`). Negative **literal** index → reject (D6); a computed negative index is a runtime trap only (§F.7). |
| `Subscript` — `Bytes[a:b]`, `Vec[…]` | **REJECT (M1) ★** | Point at `Vec.slice(lo, hi)` → `vec_slice` (`v.c`). D6 explicitly leaves "what compiles" to C. |
| `Subscript` — annotation forms `Vec[U32]`, `Map[K,V]`, `Optional[X]` | **SUPPORT (annotation position only)** | Resolved by `to_spec_type` (B7). In a value position → reject. |
| `Tuple` | **SUPPORT (event topics only)** | `events().publish((Symbol(…), addr, addr), data)` → build a `VecObject` then `contract_event` (`x.1`). D8: heterogeneous by design. Elsewhere → reject (tuple structs and `SC_SPEC_TYPE_TUPLE` are deferred, B16/S13). |
| `List` / `Dict` / `Set` displays | **SUPPORT only as the items argument of `Vec(...)`/`Map(...)`; REJECT elsewhere** | "build a `Vec(U32, [...])` / `Map(Symbol, U32, [...])`; there is no Python list/dict/set on chain". |
| `ListComp` / `SetComp` / `DictComp` / `GeneratorExp` | **REJECT** | S15's headline. "comprehensions are not supported; build the container with `Vec(T, [...])` or fill it in a `while` loop". |
| `JoinedStr` / `FormattedValue` (f-strings) | **REJECT** | "f-strings are not supported: there is no runtime string formatting host function. `String` literals are compile-time constants." |
| `Lambda` | **REJECT** | "lambdas and closures are not supported". |
| `NamedExpr` (`:=`) | **REJECT ★** | "assign on its own line". |
| `Starred` | **REJECT** | "argument unpacking is not supported; a contract export has a fixed arity in `contractspecv0`". |
| `Yield` / `YieldFrom` | **REJECT** | "generators are not supported". |
| `Slice` (as a node) | **REJECT (M1)** | Consequence of the slicing line above. |

### B.3 Declaration-shape rejections C must raise source-located

`src/serpent/decorators.py` and `src/serpent/spec/sections.py` already enforce these at class-creation / emission time as bare `ValueError`s with **no line number**. C must catch each and re-report it against the AST node:

`self`-not-first (decorators.py:318-324) · `*args`/`**kwargs` on an export (328-339) · default parameter values (340-345) · missing param annotation (346-350) · missing return annotation (353-357) · `__init__` not `-> None` (362-367) · `staticmethod`/`classmethod` (287-299, D7) · bare-int `@contracterror` member (154-167, S10) · error code out of `[0, 0xFFFFFF00)` (126-131, A9) · duplicate error code (132-136) · empty error enum (140-145) · non-chain field annotation (225-228, B7) · re-decoration (236-251) · `@contractevent` missing the `Event` base (209-214, D8) · name > 30 / non-Symbol charset (371-386, S12, D10) · type name > 60 and case name > 60 (sections.py:70-77) · doc > 1024 bytes (B12) · unresolvable annotation (`NameError` → 400-404) · **`__constructor` name and every parameter name, which the decorators never check** (B11) · every `to_spec_type` unmappable (B7).

---

## C. IR SHAPE PROPOSAL

### C.1 Design principle: one thin `HostCall` node

`HostFn` already computes `val_typed_args`, `val_typed_ret`, `wasm_params`, `wasm_result` (B3) and bindings are looked up by name (B2). **Recommendation: the IR's escape hatch is a single `HostCall(fn_name: str, args: [Expr], ty: Ty)` node**, and every `Env`/storage/auth/event/container operation lowers to it in C. D then special-cases only: control flow, literal pooling + data layout, i128/u128 arithmetic (guest runtime), overflow checks, ABI prologues, and `StorageType`/`ContractTtlExtension` raw-scalar immediates. Two payoffs: the node count stays ~30 instead of ~90, and the protocol floor (B4/S18) becomes a one-line IR walk — `declared_protocol({n.fn_name for n in ir.walk(HostCall)}, requested)`.

### C.2 Node inventory

```
Loc      = (path, line, col, end_line, end_col)          # never optional; P2
LocKind  = NODE | WHOLE_FILE                              # WHOLE_FILE renders "path:" with no line

Ty       = Bool | U32 | I32 | U64 | I64 | U128 | I128 | Timepoint | Duration
         | Symbol | String | Bytes | BytesN(n) | Address
         | Vec(Ty) | Map(Ty, Ty) | Struct(name) | Option(Ty) | Void
         | ErrorEnum(name)                                # raise-position only (S8)
   .repr        -> IMMEDIATE | HOST_OBJECT | EITHER        # from val.py bounds (A3); drives obj_cmp vs i64.eq
   .scval_rank  -> int                                    # A8 table, for any C-side ordering
```

**Expressions** (every node carries `loc: Loc` and `ty: Ty`):

`Const(py_value, ty)` — one unified literal node; pooling is D's decision · `ParamRef(index, name)` · `LocalRef(slot, name)` · `ConstRef(module_const_name)` (P5) · `Unary(op, operand)` · `Binary(op, lhs, rhs)` · `Compare(op, lhs, rhs) -> Bool` (carries `via_obj_cmp: bool`, T5) · `BoolOp(op, [Expr]) -> Bool` (short-circuit) · `IfExp(cond, then, orelse)` · `IsZero(operand) -> Bool` (D3's explicit truthiness node, so D never re-derives it) · `MakeStruct(struct_name, [(field, Expr)])` — **fields pre-sorted ascending as byte strings** (P7) · `FieldGet(obj, field, struct_name)` · `MakeVec(elem_ty, [Expr], all_static: bool)` · `MakeMap(k_ty, v_ty, [(Expr,Expr)], all_static: bool)` · `MakeTopics([Expr])` (event topics only, D8) · `HostCall(fn_name, [Expr], ty)` · `RawScalar(int, kind)` — `StorageType`/`ContractTtlExtension` immediates from `_scalars.py` (B6) · `ErrorVal(enum, case, code)` — raise-position only · `Convert(from_ty, to_ty, operand)` — `Timepoint/Duration ↔ U64` bridges (A17) · `InternalCall(fn_name, [Expr], ty)` ★.

**Statements** (every node carries `loc`):

`LetLocal(slot, ty, init)` · `SetLocal(slot, Expr)` · `Eval(Expr)` (void `HostCall`s) · `If(cond, [Stmt], [Stmt])` · `While(cond, [Stmt])` · `Break` · `Continue` · `Raise(enum, case, code)` — lowers to `HostCall("fail_with_error", [ErrorVal])`; a dedicated node so R3's "error codes never lost to `unreachable`" is checkable structurally · `Return(Expr | None)` · `Nop`.

**Declarations**:

```
ModuleIR(path, doc, imports, consts: [ConstDecl], structs, error_enums, events,
         contract: ContractIR, helpers: [FuncIR])
StructDecl(name, doc, fields: [(name, Ty, doc)], loc)      # doc reserved; B13's gap
ErrorEnumDecl(name, doc, cases: [(name, code, doc)], loc)
EventDecl(name, doc, fields, loc)                          # topic/data split undecided, B14
ConstDecl(name, ty, value: Expr, loc)                      # module-level chain constants, P5
FuncIR(py_name, export_name, kind: EXPORT|CONSTRUCTOR|INTERNAL,
       params: [(name, Ty, loc)], ret: Ty, doc, locals: [(slot, name, Ty)],
       body: [Stmt], loc, returns_on_every_path: bool)
```

**What C must hand D beyond the tree** (R2/R4 — D needs all of it at IR-consumption time):

1. `host_fns_used: frozenset[str]` — feeds `declared_protocol` (B4) and the import section.
2. `needs_memory: bool` + the literal inventory (`symbols_over_9`, `strings`, `bytes_literals`, `struct_key_descriptor_sets`) — feeds the §5/M13 memory-export assertion and the data-section layout.
3. `runtime_parts_needed: frozenset` — `i128_add`, `i128_mul`, `overflow_check`, … (spec §6).
4. `spec_inputs: (contract_cls_or_metadata, declared_types_in_order)` — B9's "`types` is declared, not discovered"; B10's pinned entry order.
5. `diagnostics: Diagnostics` — must be empty for D to run.

### C.3 Source-location, symbol and scope model

**Location.** `Loc` is mandatory and carries the full span (E5). No synthetic `(1,0)` (P2); module-level facts ("expected exactly one `@contract` class") use `LocKind.WHOLE_FILE`. Every diagnostic renders `path:line:col` (S16) with the source line and a caret span.

**Scopes** — three levels, no nesting, no closures:
- **Module**: `serpent` imports (A22), module-level chain constants (P5), decorated class names, module-level helper names ★.
- **Contract class**: method names only. There are no class attributes and no `self` state; `self` binds but any use is a diagnostic.
- **Function**: params (index 0 = `self`, ignored; index 1 = `env: Env`, dropped from the spec per `sections.py:226-228`) + locals.

**Locals** — flat slot list per function (no block scoping). Rules C enforces:
1. **Single-typed**: the first binding fixes the type; rebinding at a different type is an error (no union locals).
2. **Definite assignment**: a local bound in only one branch of an `if` and read afterwards is an error.
3. **Definite return**: a non-`Void` method must return on every path — proved in C, not left to D (P6, S17).
4. **Shadowing** a param, module constant, imported name or type name is an error.

### C.4 The Env-API recognition table

Host functions named by binding name with `module.export` shown for orientation only — **C must look them up by name (B2)**. `StorageType` immediates come from `_scalars.STORAGE_TYPE` (B6).

| Authoring surface (`src/serpent/env.py`, `types/address.py`) | Lowering |
|---|---|
| `env.storage()` / `.instance()` / `.persistent()` / `.temporary()` | No code. Resolves the `StorageType` immediate: instance=2, persistent=1, temporary=0. |
| `<bucket>.set(key, value)` | `put_contract_data(k: Val, v: Val, t: StorageType)` — `l._` |
| `<bucket>.get(key, T)` | `get_contract_data(k: Val, t: StorageType) -> Val` — `l.1`, then a narrow-to-`T` check. Missing key with no `default` → contract error; **C must allocate the reserved code** (§E14). |
| `<bucket>.get(key, T, default=d)` | `has_contract_data` (`l.0`) → `If` → `get_contract_data` (`l.1`) else `d`. (The spike carried this as a distinct `LoadDurable.default` field, `frontend.py:90-95`.) |
| `<bucket>.has(key)` | `has_contract_data(k, t) -> Bool` — `l.0`. Returns chain `Bool` (D7). |
| `<bucket>.del_(key)` | `del_contract_data(k, t)` — `l.2` |
| `instance().extend_ttl(threshold, extend_to)` | `extend_current_contract_instance_and_code_ttl(threshold: U32Val, extend_to: U32Val)` — `l.8` (no key: S24/env.py:128-133) |
| `persistent()/temporary().extend_ttl(key, threshold, extend_to)` | `extend_contract_data_ttl(k, t, threshold, extend_to)` — `l.7`. (`l.f` `extend_contract_data_ttl_v2` is protocol-gated ≥ 26 — do not reach for it silently.) |
| `env.ledger().timestamp()` | `get_ledger_timestamp() -> U64Val` — `x.4`, typed `U64` (D7) |
| `env.ledger().sequence()` | `get_ledger_sequence() -> U32Val` — `x.3` |
| `env.events().publish(topics_tuple, data)` | `MakeTopics` → `VecObject` (`vec_new_from_linear_memory` `v.g`, or `vec_new` `v._` + `vec_push_back` `v.6`), then `contract_event(topics: VecObject, data: Val)` — `x.1`. Enforce `topics[0]` is a short `Symbol` (S11). |
| `<Event instance>.publish(env)` | **Undecided** — B14/D8: no topic/data split exists (§E12). |
| `addr.require_auth()` | `require_auth(address: AddressObject)` — `a.0` |
| `addr.require_auth_for_args(vec)` | `require_auth_for_args(address, args: VecObject)` — `a._` |
| `raise Error.X` | `fail_with_error(error: Error)` — `x.5`, arg = `val.error_val(code)` (S7, R3) |
| `Symbol("…")` ≤ 9 chars | SymbolSmall immediate via `val.symbol_small` (S22) |
| `Symbol("…")` > 9 chars | `symbol_new_from_linear_memory(lm_pos, len)` — `b.j` (S19) |
| `String("…")` | `string_new_from_linear_memory(lm_pos, len)` — `b.i` |
| `Bytes(b"…")` | `bytes_new_from_linear_memory(lm_pos, len)` — `b.3` |
| struct construction | `map_new_from_linear_memory(keys_pos, vals_pos, len)` — `m.9`; keys are compile-time-sorted `(u32 ptr, u32 len)` descriptors (P7) |
| struct field read | `symbol_new_from_linear_memory` (`b.j`) if > 9 chars, then `map_get(m, k)` — `m.1` |
| `Vec` ops | `vec_new` `v._`, `vec_put` `v.0`, `vec_get` `v.1`, `vec_del` `v.2`, `vec_len` `v.3`, `vec_push_front` `v.4`, `vec_pop_front` `v.5`, `vec_push_back` `v.6`, `vec_pop_back` `v.7`, `vec_front` `v.8`, `vec_back` `v.9`, `vec_insert` `v.a`, `vec_append` `v.b`, `vec_slice` `v.c`, `vec_first_index_of` `v.d`, `vec_last_index_of` `v.e`, `vec_new_from_linear_memory` `v.g` |
| `Map` ops | `map_new` `m._`, `map_put` `m.0`, `map_get` `m.1`, `map_del` `m.2`, `map_len` `m.3`, `map_has` `m.4`, `map_key_by_pos` `m.5`, `map_val_by_pos` `m.6`, `map_keys` `m.7`, `map_values` `m.8`, `map_new_from_linear_memory` `m.9` |
| `Bytes` ops | `bytes_get` `b.6`, `bytes_len` `b.8`, `bytes_slice` `b.f`; `string_len` `b.k`, `symbol_len` `b.l` |
| comparison on host-object types | `obj_cmp(a: Val, b: Val) -> i64` — `x.0`; **`val_typed_ret=False`** (raw scalar, B3) |
| `U128`/`I128` values | `obj_from_u128_pieces` `i.3` / `obj_to_u128_lo64` `i.4` / `obj_to_u128_hi64` `i.5`; `obj_from_i128_pieces` `i.6` / `obj_to_i128_lo64` `i.7` / `obj_to_i128_hi64` `i.8`. Arithmetic is guest runtime; div/rem via i256 (`obj_from_i256_pieces` `i.g`, `i256_div` `i.y`) — spec §6 |
| `U64`/`I64` outside the small range | `obj_from_u64` `i._` / `obj_to_u64` `i.0`; `obj_from_i64` `i.1` / `obj_to_i64` `i.2` |
| `Timepoint`/`Duration` bridges | `timepoint_obj_from_u64` `i.D`, `duration_obj_from_u64` `i.F` (A17) |

**Recognized but not lowerable in M1-C** (must be a clean "not in M1" diagnostic, not a crash): `env.logs()` (`log_from_linear_memory` `x._`), `get_current_contract_address` `x.7`, `get_max_live_until_ledger` `x.8`, `get_ledger_version` `x.2`, `get_ledger_network_id`, `call`/`try_call`, crypto, PRNG, deployer — all M2 (spec §11).

---

## D. DIAGNOSTICS DESIGN INPUTS

### D.1 Error-code taxonomy — recommend YES, stable codes

Recommendation: a stable `SPT####` registry, because S14 mandates docs generated from `must_reject/` and codes keep that table stable under message rewording, and B15 already sets the precedent of a structured error naming every offender. Cost to accept explicitly: **the codes become public API**. Proposed banding:

| Band | Domain |
|---|---|
| `SPT1xxx` | Unsupported construct (the §B REJECT column; one code per construct family) |
| `SPT2xxx` | Name resolution / imports / scope (unresolved name, `self` use, shadowing, non-`serpent` import) |
| `SPT3xxx` | Types (cross-type arithmetic, out-of-range literal, omitted operator, unmappable annotation, `Error`-as-return, truthiness of a non-numeric) |
| `SPT4xxx` | Contract shape (`self`-first, annotations, defaults/varargs, `__init__ -> None`, static/classmethod, decorator misuse, error-code range/uniqueness, struct field assignment) |
| `SPT5xxx` | Spec/XDR limits (name ≤ 30/≤ 60, doc ≤ 1024, tuple arity, Symbol charset) |
| `SPT6xxx` | Protocol gating (B4/B5/S18) |
| `SPT7xxx` | Flow analysis (missing return on a path, use-before-definite-assignment, `break`/`continue` outside a loop, unreachable code) |

### D.2 Source-located error format

Seed: `SpikeCompileError(msg, lineno, col)` with `__str__ = f"{msg} (line {lineno}, col {col})"` (`spikes/spike1/frontend.py:19-29`). Generalize to a **structured** diagnostic — never a pre-formatted string, so `must_reject/` can assert on `code` + substring and the CLI can render:

```python
@dataclass(frozen=True)
class Diagnostic:
    code: str          # "SPT1003"
    loc: Loc           # mandatory; LocKind.WHOLE_FILE for module-level facts (P2)
    message: str       # one line, lowercase, no trailing period
    help: str | None   # the rewrite that works
    notes: tuple[str, ...] = ()
```

Rendering (mypy/ruff/rustc-shaped, and what `stellar serpent build` prints):

```
contracts/token.py:14:16: error[SPT1003]: comprehensions are not supported
   14 |         totals = [b * U32(2) for b in balances]
      |                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
   help: build the container explicitly -- Vec(U32, [...]) -- or fill it in a `while` loop
   note: the supported subset is documented at docs/subset.md#comprehensions
```

`CompileError(Exception)` carries `diagnostics: tuple[Diagnostic, ...]`. serpent's error convention holds (A10): it subclasses `ValueError` so a caller can catch one class of failure from the whole build, matching `SpecTypeError`/`SpecNameError`/`SpecDocError`/`ProtocolGateError`.

**Multi-diagnostic collection — recommend YES**: a `Diagnostics` sink that collects and continues at per-method granularity (one error per compile is poor DX for a 200-line contract), reporting all at once, sorted by `loc`. Contestable on complexity grounds (§E16).

**Decorator-error bridging (concrete requirement).** If C imports the contract module (§E1), every check in `decorators.py`/`sections.py` fires as a location-free `ValueError`. C must catch it, match the offending class/method/field name back to its AST node, and re-report it as a located `SPT4xxx`/`SPT5xxx`. Without this the best error in the toolchain is a bare traceback.

### D.3 `must_reject/` executable-spec mechanics

**Layout** (`tests/must_reject/` per spec §3):

```
tests/must_reject/
├── README.md                      # the contract: how to add a case
├── constructs/                    # SPT1xxx -- one file per construct family
│   ├── comprehension_list.py
│   ├── fstring.py
│   ├── try_except.py
│   ├── closure_nested_def.py
│   ├── lambda_expr.py
│   ├── async_def.py
│   ├── global_stmt.py
│   ├── with_stmt.py
│   ├── match_stmt.py
│   ├── assert_stmt.py
│   ├── del_stmt.py
│   ├── dict_display.py
│   ├── slice_bytes.py
│   ├── walrus.py
│   ├── star_args.py
│   ├── chained_compare.py
│   └── ...
├── names/                         # SPT2xxx
├── types/                         # SPT3xxx -- seeded from cases.py's 20 reject cases (T1)
├── shape/                         # SPT4xxx -- seeded from decorators.py's checks
├── limits/                        # SPT5xxx
├── protocol/                      # SPT6xxx
└── flow/                          # SPT7xxx
```

**File format** — a machine-readable header the runner parses, so the expectation lives next to the code (S14):

```python
# serpent:reject SPT1003
# serpent:at 9:17
# serpent:message comprehensions are not supported
# serpent:doc-title list comprehension
from serpent import U32, Env, Vec, contract

@contract
class C:
    def totals(self, env: Env, v: Vec[U32]) -> Vec[U32]:
        return Vec(U32, [x + U32(1) for x in v])
```

**Runner** (`tests/must_reject/test_must_reject.py`):
1. Glob `**/*.py` (excluding `test_*`), read each as **text** and call `compile_module(source, path)`. **Never import the fixture** — importing executes decorators and defeats the point.
2. Assert exactly one `CompileError`; assert the declared `code`, the declared `line:col`, and that the declared `message` is a substring of the rendered diagnostic.
3. Meta-test A: every declared code exists in the code registry.
4. Meta-test B: **every code in the registry has ≥ 1 fixture** — completeness, which is what makes the directory a specification rather than a sample.
5. Meta-test C: every `kind="reject"` case in `tests/semantics/cases.py` (T1) has a corresponding fixture or is covered by an expression-level frontend assertion.

**Docs generation** (S14, spec §12's drift mitigation). A `docs/gen_subset.py` (mkdocs-macros or a pre-build hook) walks the directory and emits the "unsupported constructs" table — `doc-title` | code | message | the offending snippet | the `help:` rewrite — grouped by band. Docs and compiler cannot drift because both read the same files. The same generator emits a code index page.

**Tooling-scope decision C must make (§E15).** `[tool.mypy] files = ["src","tests"]` with no excludes, `[tool.ruff] src` includes `tests`, and CI runs `ruff format --check src tests` (E1, E2). Rejection fixtures will fight all three: some are valid mypy (comprehensions, f-strings) but many are deliberately invalid Python-as-typed (bare-int error members, unannotated params, `raise` of a non-exception). Concrete requirement: add `tests/must_reject` to `[tool.mypy] exclude`, add a `"tests/must_reject/**" = ["ALL"]` ruff per-file-ignore, exclude the directory from `ruff format --check`, and keep filenames non-`test_*` so pytest never collects them directly. There is precedent for the *inverse* (E3) so the exclusion must be deliberate and commented, not silent.

---

## E. OPEN QUESTIONS FOR THE CONTROLLER

**E1 — AST-only, import-the-module, or hybrid? (the biggest architectural fork in C.)**
Options: (a) pure `ast.parse` (spike style, P3); (b) import the module and read `_serpent_type_` (A19) + `typing.get_type_hints`; (c) hybrid — import for declarations, AST for bodies.
Forces toward (b)/(c): `build_spec_entries` **takes the executed class** and reads `_serpent_type_` (B9, `sections.py:134,160-161`); PEP 563 makes AST annotations strings and `get_type_hints` is the sanctioned resolver (E4, `decorators.py:302-313`); `bytes_n(20)` in an annotation is unresolvable without evaluation (A16); `errorcode(N)` codes are trivially readable from either (A20).
Cost of (b)/(c): the build executes user module-level code; the two views can skew.
**Recommendation: (c) hybrid** — import for declarations/spec/annotations, AST for method bodies, plus a mandatory cross-check that the metadata method/param/type inventory and the AST inventory agree exactly (a skew there is a compiler bug, not a user error). Document build-time execution prominently.

**E2 — `Vec[Settings]` TypeVar bound.** `Vec`'s `T` is bound to `types._ordering.ChainValue`, a Protocol requiring `_SCVAL_RANK`/`_cmp_payload`, which `@contracttype` structs do **not** satisfy — so `Vec[Settings]` needs `# type: ignore[type-var]` even though `to_spec_type` maps it correctly (`tests/unit/test_typemap.py:190-196`). Options: (a) give decorated structs `_SCVAL_RANK = 17` + `_cmp_payload()` — but a decorator cannot make mypy see them; (b) widen `T`/`K`/`V` to a bound that includes `Struct` (mirroring `env.ChainValue`, `env.py:63`); (c) leave the ignore and have C reject `Vec[Struct]`.
**Recommendation: (b)** — a small M1-A follow-up landed inside C. `Vec[Balance]` is an ordinary contract shape and (c) would make the flagship token example unwritable.

**E3 — Struct-as-storage-key vs the `val_cmp` deferral.** D7 widened storage keys to include `@contracttype` instances and `tests/fixtures/token_style.py:83-100` ships `BalanceKey(owner=…)` as the dominant pattern — but structs have no `_SCVAL_RANK` at all, so `val_cmp` cannot order them and tier 1 rejects them as `Map` keys (`_ordering.require_chain_value`). Options: (a) C allows struct keys (on chain a struct is a `MapObject`, rank 17, which the host orders fine) and accepts that tier 1 simply cannot answer ordering questions about them, pinning the behaviour in tier-2b differentials instead; (b) give structs rank 17 + a payload so tier 1 can order them — risks *inventing* an order that A15 says is unverified; (c) restrict M1-C storage keys to scalars.
**Recommendation: (a)** with an explicit "not modelled in tier 1" note in the IR and the docs. (c) breaks `token_style.py`; (b) invents on-chain semantics, which A15 forbids.

**E4 — `for x in vec` in M1-C or later?** It is #1 in spec §2's list (S15) and desugars entirely inside C (`vec_len` + `vec_get` + induction local) so D never sees a `For`. **Recommendation: in M1-C.** Fallback if descoped for schedule: a reject whose `help:` line contains the literal while-loop rewrite.

**E5 — `for i in range(...)`?** **Recommendation: support `range(stop)` and `range(start, stop)`** with the same desugaring; reject the 3-arg and negative-step forms in M1 (they need a signed step and a direction check for no benefit yet).

**E6 — Augmented assignment.** `count += U32(1)` is what everyone writes. **Recommendation: support `+= -= *= //= %=`** by desugaring to `x = x <op> y` on locals of arithmetic chain types; reject the bitwise/pow forms consistently with A5/D2.

**E7 — Early-return placement.** Options: (a) `return` anywhere, with C proving definite return on every path; (b) tail position only (the spike's implicit rule). **Recommendation: (a)** — spec §4 names early returns as precisely the spike bug class that "must be structurally impossible", `wasm-tools validate` provably cannot catch it (P6), and the analysis is small. (b) would make guard-clause style — the natural way to write `transfer` — impossible.

**E8 — Recursion and cross-method calls within a contract.** Note that spec §2's own example calls a **module-level** `balance(env, from_)`, so "exports are leaves" contradicts the spec's illustration. Options: (a) reject all internal calls in M1-C; (b) allow module-level private functions and `self._helper(...)` private methods as internal WASM functions, **reject recursion** via a call-graph cycle check; (c) allow recursion too.
**Recommendation: (b)** — code sharing is unavoidable in a token contract, and rejecting cycles keeps stack-depth and budget reasoning trivial. Requires the `InternalCall` IR node and a `kind=INTERNAL` `FuncIR`, both cheap for D (a non-exported WASM function).

**E9 — `and`/`or`/`not` typing.** Python's `and`/`or` return an *operand*, not a bool, so `U32(0) and U32(5)` is `U32(0)` — there is no sound single-type lowering for mixed operands. **Recommendation: restrict `and`/`or`/`not` to `Bool`-typed and comparison operands, result `Bool`**, short-circuited; reject everything else naming the reason. Contestable: one could allow same-type operands with value-returning semantics, at the cost of a surprising `Bool`/`U32` result type.

**E10 — Truthiness scope.** D3 mandates lowering chain-int truthiness to a zero-test, and `bool(x)` appears throughout the semantics table. But `Symbol`/`String`/`Bytes`/`Vec`/`Map`/`Address`/structs have **no `__bool__`**, so `if vec:` is silently `True` forever at tier 1 — a genuine trap. **Recommendation: support truthiness only for numeric chain types and `Bool`; make truthiness of every other chain value a compile error** naming the explicit test to write (`len(vec) > U32(0)`, `storage.has(k)`). D3's alternative ("a compile-reject could replace it later") is *not* recommended: it breaks `if amount:`.

**E11 — Container mutation and aliasing (the sharpest correctness question in C).** The host's Vec/Map ops are **functional** — `vec_push_back(v, x) -> VecObject`, `map_put(m, k, v) -> MapObject` — while `serpent.types.Vec.push_back` mutates in place and returns `None`. So `v.push_back(x)` must lower to `v = vec_push_back(v, x)`, which is only sound when C owns the binding. `a = b; a.push_back(x)` mutates `b` at tier 1 and does not on chain: a **silent** divergence. Options: (a) allow mutating methods only on a local whose binding C controls, and reject mutation through a parameter, a field, a subscript, or any local aliased from another container local; (b) reject container mutation entirely in M1-C (construction + read only); (c) change the authoring surface to functional (`v = v.push_back(x)`) — breaking, and `Vec` semantics are already frozen by A18/D2.
**Recommendation: (a)**, with the alias analysis written as an explicit, tested pass and a diagnostic that explains the functional-host-op reason. Flagging hard: this is the single most likely place for C to silently diverge from the oracle.

**E12 — `Event.publish(env)` lowering.** `env.events().publish(topics, data)` is fully specified (D8, `x.1`); `Transfer(...).publish(env)` is not — `_serpent_type_` carries no topic/data split, and B14 defers `SCSpecEventV0` to sub-plan E precisely because guessing "would ship a valid-but-lying spec". Options: (a) M1-C recognizes only `env.events().publish(...)` and rejects `Event.publish(env)` with "deferred to sub-plan E"; (b) C defines the split now (e.g. an `Annotated`/marker on topic fields) — which decides an E-owned question; (c) C guesses a convention (e.g. all fields are data, name is the topic).
**Recommendation: (a)** — but note `tests/fixtures/token_style.py:105` already calls `Transfer(...).publish(env)`, so (a) means that line does not compile in M1-C. The controller should decide whether the fixture is amended or the split is decided early.

**E13 — Raw `str`/`bytes` operand coercion in `==`** (T4, explicitly assigned to C). Options: (a) reject `Symbol("abc") == "abc"` and `Bytes(b"abc") == b"abc"` at compile time — making the two `tier1_only` cases permanently tier-1-only and settling the undecided tier-2 answer; (b) support it as `False` (matching tier 1, which never raises per A7); (c) coerce and compare payloads (contradicts tier 1).
**Recommendation: (a)** — S1 says reject rather than approximate, and a comparison that is *always* `False` is a bug the compiler should name.

**E14 — Reserved runtime error codes C must allocate.** Only 2 of the 256 codes in `[0xFFFF_FF00, 0xFFFF_FFFF]` are used (A9). C should own the registry and allocate at minimum: missing storage entry (no `default`), ABI tag/range check failure (spec §4's prologue), unreachable-return guard, and an explicit "unsupported at runtime". **Recommendation: C defines a `serpent/errors.py` registry addition with one code per distinct failure and a documented table; D emits them.** Contestable: whether ABI-prologue failures get one code or one per argument position.

**E15 — `must_reject/` under mypy/ruff/pytest scope.** See §D.3. Needs an explicit config decision because there is precedent for the opposite (E3).

**E16 — Fail-fast vs collect-all diagnostics.** Recommend collect-all at per-method granularity; contestable on implementation complexity and on error-cascade quality.

**E17 — Stable error codes vs messages-only.** Recommend codes (§D.1); the cost is that they become public API.

**E18 — Slicing.** D6 leaves "what compiles" to C. Recommend: support `Vec.slice(lo, hi)` → `vec_slice` (`v.c`) as a method; reject `bytes[a:b]`/`vec[a:b]` subscript slices in M1 with a `help:` naming the method form. Contestable — `bytes[a:b]` is idiomatic and `bytes_slice` (`b.f`) exists.

**E19 — `len()`'s type.** Tier 1's `__len__` must return a Python `int`; the compiler wants `U32`. Recommend the compiler types `len(x)` as `U32` and the one-way divergence is documented (§F.4). No alternative exists inside Python's protocol.

**E20 — `bytes_n(N)` annotations** (A16's banked item). Recommend supporting them, which follows from E1's hybrid approach (the class is a real object with `_LENGTH`, and `to_spec_type` already keys off `_LENGTH`, B7/B8). Under a pure-AST frontend this needs a literal-N special case in annotation position.

---

## F. RISKS

### F.1 Where C can silently diverge from the tier-1 oracle

Ordered by likelihood × silence.

1. **Container mutation / aliasing** (§E11). Functional host ops vs in-place Python mutation. `a = b; a.push_back(x)` gives different answers on the two tiers with no error on either. **The highest-severity silent divergence in C.**
2. **`Symbol` ordering** (T5). Tier 1 orders small Symbols by raw ASCII bytes; the host's `SymbolSmall` packs 6-bit alphabet codes where `_`=1 and `A`=12. If C lowers small-Symbol comparison to a raw `i64` compare of the packed Val (the obvious optimization, since both are immediates), `Symbol("_") < Symbol("A")` flips relative to `cases.py:415-425`. **Mitigation: route every `Symbol` comparison through `obj_cmp` (`x.0`) unless equivalence is proven by differential test.**
3. **Truthiness of non-numeric chain values** (§E10). Tier 1: always `True` (Python object default). Compiler: must reject. If C instead lowers it to "non-null handle → true", `if empty_vec:` silently agrees with tier 1 and disagrees with intent.
4. **`len()` typing** (§E19). `int` at tier 1, `U32` in the compiler — a permanent, documented one-way divergence.
5. **`has()` return type asymmetry.** `env.storage()…has(k) -> Bool` (chain, D7) but `Map.has(k) -> bool` (plain, `containers.py:312`). Both truthy, so `if …has(k):` compiles either way — but a contract that returns the result gets different types. C must type each precisely.
6. **`bool` as an int operand** (D4, T2). Tier 1 accepts `U32(5) + True` because `bool ⊂ int`; the compiler must reject. Already marked `tier1_only` — the risk is a *new* case drifting onto that ground; the regex tripwire (T3) exists for exactly this and must be replaced, not deleted.
7. **Negative indices.** C can only reject negative **literals** (D6). A computed negative index is a runtime `IndexError` at tier 1 and a host trap on chain — same class, but C must not claim to have proved it statically.
8. **`Bytes` family subclass asymmetry.** `Bytes32(p) == Bytes(p)` is `True` (D5), but `Vec(Bytes32).first_index_of(Bytes(p))` is a tier-1 `TypeError` even though the host would find the element (`containers.py:71-80`). C must reproduce the tier-1 *strictness*, not the host's permissiveness, or the oracle diverges.
9. **Timepoint/Duration arithmetic.** Both are i64 underneath, so a naive lowering "just works" while tier 1 raises `TypeError` (D4, A17). C must reject on the declared type, never on the representation.
10. **Constant folding.** If C folds `I32(2**31-1) + I32(1)` into a *compile* error, tier 1's answer (a runtime `ArithmeticOverflow`, `cases.py:188-193`) becomes unreachable and the case would have to be reclassified. **Recommendation: C does no arithmetic constant folding. S3's compile-time bounds checks apply to literal *coercion* (`U32(2**32)`, `U32(5) + 2**32`), not to expression results.**
11. **`I32`/`U32` on a 64-bit stack.** Everything is i64 at the ABI (B3/B18), so `I32` ops need explicit narrowing/sign-extension. If C types an `I32` expression as `I64`, `-7 // 2` and the `MIN`-boundary cases still pass while `I32` overflow silently stops firing. C must carry the exact declared width in `Ty`.
12. **`Map` iteration order.** Observable on-chain (A14). Any C-side reordering of a `Map(...)` literal's pairs, or a `for k in map` desugaring that walks insertion order rather than `map_keys` (`m.7`), diverges.
13. **Struct field order.** `map_new_from_linear_memory` requires keys sorted ascending as **byte strings** at compile time (P7). If C emits declaration order and D re-sorts (or doesn't), the module validates and panics on-chain. C should own the sort and record it in `MakeStruct`.
14. **Import/AST skew** (§E1). Under the hybrid design, a method present in `_serpent_type_` but absent from the AST (or vice versa) yields a spec that doesn't match the code. Must be a hard internal assertion.

### F.2 Differential checks that belong in C's own test plan

1. **Semantics-table classification (the roadmap's named obligation, T3).** Add `compile_expression(source)` and assert, for every `SemCase`: every `kind="reject"` case is **rejected** by the frontend with a located diagnostic; every non-`reject`, non-`tier1_only` case **type-checks and lowers** cleanly. This replaces the three regexes in `tests/semantics/test_semantics.py:63-87` with real checks. C is the *only* tier that can prove the reject side (T6).
2. **`tier1_only` ⟺ frontend-rejects meta-test.** For the four `tier1_only` cases (T2), assert the frontend rejects the source. For every non-`tier1_only` case, assert it does not. Drift in either direction fails.
3. **`must_reject/` completeness both ways** (§D.3 meta-tests A/B/C): every fixture's code exists; every registered code has a fixture; every `cases.py` reject case is represented.
4. **AST-allowlist property test.** Walk every file C *accepts* and assert it contains no `ast` node type outside the SUPPORT set of §B. This prevents accidental support creep — the exact "scope creep toward real Python" risk in spec §12.
5. **Robustness fuzz.** Any `ast.parse`-valid random Python must produce a `CompileError` with a real `Loc` — never an internal traceback, `KeyError`, `AttributeError`, or `IndexError`. (P2/P3 are the historical failure mode; `frontend.py:519` would die on a module docstring.)
6. **Host-fn-set ↔ protocol cross-check.** For each M1 example, feed C's collected `host_fns_used` to `_host.declared_protocol` and assert it equals the protocol the build declares (B4/S18). Independently assert the used set never contains a function whose `min_protocol` exceeds the target, with the diagnostic naming it (B5).
7. **Spec-view cross-check.** For each example, assert C's AST-derived method/param/type inventory matches `build_spec_entries(cls, types=…)`'s metadata-derived view, including `__constructor`-first ordering (B10) and the dropped leading `env` (B9/`sections.py:226-228`). Catches F.1.14.
8. **`token_style.py` as a frontend fixture.** `tests/fixtures/token_style.py` is the only complete authored contract in the repo (A23) and currently only proves the mypy claim. It must compile clean through C — modulo the `Event.publish` decision (§E12).
9. **Phase 0 host-fn-set golden.** Re-author `spikes/spike1/contract_src.py` in `self`-first style (never modify the spike, R5) and assert C's `host_fns_used` equals the import set of the on-chain-verified 877-byte artifact (`CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI`). This is the only end-to-end anchor available to C before D exists.
10. **Golden IR snapshots** for the M1 examples, so a refactor that changes lowering shows up as a reviewable diff rather than a silent behavioural change.
11. **Diagnostics-quality assertions.** Every `Diagnostic` must have a non-empty `help` for `SPT1xxx` (unsupported construct) — the class of error where the user needs the rewrite, per S15's "a *good* error".
12. **Decorator-error bridging test.** For each check in `decorators.py`/`sections.py`, assert C surfaces a **located** diagnostic rather than the bare `ValueError` — the concrete requirement in §D.2.

### F.3 Process risks

- **Reversal costs come due in C.** D2, D3, D5, D6, D7 all record "reversal cost: low **before** sub-plan C". Once C's pattern-matching is written, the authoring surface is effectively frozen. Any surface change the controller wants (E2, E12) is cheapest inside C's plan, not after.
- **The `~25 constructs` estimate is low.** §B enumerates ~55 distinct AST-node lines. R1's "~25" should be read as the *user-facing* count, not the implementation count.
- **`spikes/` is frozen (R5).** C must copy values, never import — `spikes/spike1/harness.py` additionally carries a stale Val object bound (A11) that must not propagate.
- **`decisions.md` is controller-owned** (B9/M1-B Global Constraints, line 82): C's plan records rulings in its own ledger and the controller promotes the lasting ones.
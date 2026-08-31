# M1-E2 DESIGN-INPUTS DOSSIER — tagged unions + int enums (the late-M1 addendum)

> Compiled by a research agent 2026-08-31. Format model:
> `docs/superpowers/specs/2026-08-27-m1c-inputs-dossier.md` (canonical) and
> `docs/superpowers/specs/2026-08-28-m1e-inputs-dossier.md` (most recent). This
> document is the citation target for the M1-E2 plan. **Nothing here is a
> ruling** — §E poses the questions the controller rules on; every §A line is a
> frozen input, every §B/§C line is either probe-verified live or explicitly
> flagged as unverifiable, and every §D line is a proposal.
>
> Repo root: `/Users/elliotvoris/Dev/stellar/sdk/py-soroban`. Branch `main`, HEAD
> `eae288a`. Absolute paths where a path is load-bearing; `src/`-relative
> elsewhere for readability.

Sources read in full: both predecessor dossiers; spec §2 (lines 90–132), §4, §5,
§7, §11, §13; the M1 roadmap (rows A–G + standing constraints);
`docs/superpowers/decisions.md` (the 2026-08-31 entries, the M1-E rulings E1–E10
+ plan-review, the M1-D rulings, the M1-C rulings E16/E17, D9's registry
discipline); `.superpowers/sdd/2026-08-28-m1e-env-runtime/final-review-attention.md`
in full; `docs/superpowers/process.md:38-51`; `src/serpent/decorators.py`,
`src/serpent/spec/typemap.py`, `src/serpent/spec/sections.py`,
`src/serpent/compiler/{loader,recognize,decls,types_,ir,codes,frontend}.py`,
`src/serpent/emitter/lower.py`, `src/serpent/env.py`,
`src/serpent/types/{_ordering,_storage_key,containers,_base}.py`,
`src/serpent/__init__.py`; `tests/fixtures/token_style.py`,
`tests/fixtures/env_surface.py`, `tests/goldens/README.md`,
`tests/unit/test_no_stale_promises.py`, `tests/unit/test_bridging_completeness.py`,
`examples/events.py`, `docs/subset.md` (structure + the SPT3019 section).

**Live verification performed** (read-only; nothing written outside
`/private/tmp/m1e2`, since deleted): 24 throwaway contract modules compiled
through the public `compile_module` API and two through `build_wasm`; ten
candidate authoring-surface files type-checked under `uv run mypy --strict`; real `SCSpecUDTUnionV0` /
`SCSpecUDTEnumV0` / `SCSpecEntry` instances constructed and XDR-round-tripped
against the pinned `stellar_sdk` 15.0.0; the Rust
`soroban-sdk-macros/src/derive_enum.rs` and `derive_enum_int.rs` fetched from
`stellar/rs-soroban-sdk@main` and read directly, cross-checked against a DeepWiki
query on the same repo. Facts so obtained are marked **[probe-verified]**.

**Cross-dossier IDs.** `S#/R#/D#/X#/Q#/P#` below are this dossier's own banks.
Where the evidence trail runs back into a predecessor's bank, that ID is used
verbatim and named as such in the text (`A15`, `A23`, `B8`, `B13`, `T1`, `MJ-9`
and `MJ-11` come from the M1-A/B/C dossiers, and the code they annotate quotes
them by those names — following the M1-E dossier's practice).

**Provenance discipline used throughout:** shipped code and `decisions.md` beat
older docs; a claim taken from a docstring is quoted rather than paraphrased;
where a source and the tree disagree the disagreement is stated. Three such cases
here — (1) the 2026-08-31 scheduling entry calls the workaround "per-variant
`@contracttype` keys + Symbol constants (token_style's shape)", but
`token_style.py` models *storage-key discrimination*, not a tagged-union value,
so **there is no tagged-union workaround demonstrated anywhere in the tree**
(§C.2); (2) the M1-E triage names the `topic` marker as silently inert on a
method PARAMETER — it is also silently inert on a method RETURN annotation, and
already mis-coded on a struct field (§C.9); (3) the task brief describes the
`get(k, ty, default=0)` gap as needing `# type: ignore` on the argument — mypy
actually reports it at the *return* (§C.10).

---

## A. FROZEN INPUTS

### A.1 Spec obligations (`docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md`)

| ID | Constraint | Source |
|---|---|---|
| S1 | **The M1 scope sentence, verbatim**: "storage tiers + TTL, `require_auth`/`require_auth_for_args`, events, errors, **structs/unions/enums**, the runtime library, testing tiers 1–2, CLI build/inspect, examples …, docs site, CI." This sentence is the whole warrant for M1-E2. | §11:318–322 |
| S2 | **The on-chain conventions, verbatim**: "named-field struct → `Map<Symbol, V>`; tuple struct → `Vec<V>`; **tagged union → `Vec` led by variant-name `Symbol`; int enum → `u32`**; `@contracterror` → `u32` codes under `SCE_CONTRACT`." | §2:102–104 |
| S3 | **`@contracterror` members are exception classes declared `NAME = errorcode(N)`, never bare ints** — "static checkers never execute decorators, so the bare-int form is inferred `int` and `raise` fails `mypy --strict` (verified by live repro; **no decorator typing trick rescues it**)". This is the closest existing precedent for a per-member declaration that must be statically visible. | §2:105–115 |
| S4 | **Spec XDR limits validated at compile time with source-located errors**: function/field names ≤ 30, type names ≤ 60, docs ≤ 1024, tuple arity ≤ 12. | §2:120–124 |
| S6 | **The subset is an executable specification**: `tests/must_reject/*.py` annotated with the expected source-located error; the docs' unsupported-constructs table is **generated from that directory**. Any authoring-surface change must MOVE a fixture, not just a checker. | §2:129–132 |
| S8 | **Linear memory is required in M1** because Symbols > 9 chars (struct field names, **event names**) need `symbol_new_from_linear_memory`; the emitter pools literals in a data section. | §5 |
| S9 | **`SymbolSmall` ≤ 9 chars, `SCSYMBOL_LIMIT` = 32**; charset `_`=1, `0-9`=2…, `A-Z`=12…, `a-z`=38…. **A variant-name Symbol longer than 32 characters is not representable on chain at all.** | §13 (BINDING) |
| S10 | **Contract max size 131072 bytes; one host call ≈ 74 instructions of fixed overhead** (DispatchHostFunction 295 + VisitObject 60 + WasmInsnExec 4/instr). Relevant because a union read costs one `vec_get` per payload slot. | §13 (BINDING) |
| S11 | The §13 appendix contains **no union- or enum-specific fact**: the tag table (Vec 75, Map 76), `SCSYMBOL_LIMIT`, and the ScVal case list are the only §13 facts that touch this surface. There is no verified §13 statement about unit-variant representation — §B.1 is where that gets sourced. | §13, read in full |
| S12 | **M2 boundary, explicit**: "Cross-contract calls … crypto host functions, PRNG, deployer, TTL helpers, full SEP-41 token example, U256/I256." Unions/enums are NOT in the M2 list — they are in M1's (S1). | §11:324–326 |
| S13 | Risk register lines M1-E2 inherits: "**Subset/docs/compiler drift** — `must_reject/` is executable and generates docs"; "**Scope creep toward 'real Python'** — the subset spec is the contract; **rejections are features**"; "**`stellar_sdk` XDR coupling** — version floor and ceiling pinned; golden bytes for all three sections". | §12 |
| S14 | "The single highest-risk internal drift is between the chain-type classes' *Python runtime behavior* and the compiler's *emitted WASM behavior*" — the semantics table runs the same cases against both tiers. A new value kind adds a new place for that drift. | §10 |

### A.2 Roadmap standing constraints (`docs/superpowers/plans/2026-08-26-m1-roadmap.md`)

| ID | Constraint | Source |
|---|---|---|
| R1 | **Row B already promised UDT entries**, verbatim: "`sections` module v2 (spec/env-meta/meta via stellar_sdk XDR **incl. UDT + event entries**, name-length validation, docstrings) with golden tests". Row B shipped struct + error-enum + (in E) event entries. **Union and int-enum entries are the unshipped remainder of a row-B promise**, which is why they slot into `spec/sections.py` rather than into a new module. | line 19 |
| R2 | Row C produces "typed IR, type checker, diagnostics engine … `must_reject/` executable subset spec"; row D "production emitter … for the full M1 language". M1-E2 is an addendum that edits both. | lines 20–21 |
| R3 | Standing constraints verbatim: "single Val codec; validate-inside-compiler; error codes never lost to `unreachable`; `self`-first methods; exception-class errors; **pre-validate at every nominally-fallible soroban-sdk boundary**; balance checks at `ret()`; pinned toolchain versions with drift-detection tests; **adversarial review before execution**; SDD with task-scoped reviews." | lines 26–32 |
| R4 | **"E onward partially parallelizable."** M1-E2 is scheduled between E's merge and F (`process.md:45-51`), so F is not yet building — M1-E2 is the last sub-plan with a *sequential* successor. Anything it leaves in flux is F's problem later, not concurrently. | lines 13–14; `process.md:45-51` |
| R5 | **`spikes/` is read-only** until G's cleanup task; M1-E2 must not touch it. | lines 34–36 |
| R6 | **The M1 gate deploys one of E's five examples to testnet, user-approved (HARD STOP).** M1-E2 lands BEFORE that deploy (`decisions.md` 2026-08-31), so anything it changes about the examples is deployed. | row G; `process.md:48-51` |

### A.3 Decision-log rulings that bind M1-E2 (`docs/superpowers/decisions.md`)

| ID | Ruling | Source |
|---|---|---|
| D1 | **The scheduling ruling itself**, verbatim on scope: "a late-M1 addendum sub-plan (working name M1-E2, dossier → plan → review → SDD, the standard loop) scheduled after the M1-E merge and before M1's closing deployment. **Until it lands, the documented workaround stays per-variant `@contracttype` keys + Symbol constants (token_style's shape).**" And the sizing note: "the surface touches decorators, typemap, spec sections (union/enum UDT entries), frontend lowering, **and the emitter's descriptor inventory** — a real sub-plan, not a rider on E/F." | lines 534–553 |
| D2 | **Why, verbatim**: "the spec is the binding authority and its M1 sentence is explicit; a deferral would ship M1 incomplete against its own scope line and require amending the spec instead." Reversal: "schedule-only today (nothing is built)". | lines 545–553 |
| D3 | **The two fed items, verbatim from the M1-E final-review rulings**: "Parked with reasons …: **the SPT3019 relax-to-32 pass and the method-parameter `topic` refusal both feed M1-E2's dossier**; `from_` aliasing is a G/M2 docs item; env.py at ~1,550 lines fires E10's package-promotion trigger for M2." | lines 576–581 |
| D4 | **M1-E's E2 desugar precedent** (the single most load-bearing precedent here), verbatim: "the frontend **DESUGARS** `Event.publish(env)` into the existing `HostCall("contract_event", (MakeTopics, MakeMap/MakeVec/value))` — **the IR and the emitter change NOT AT ALL**". Also from the same ruling: events appended AFTER functions in spec entry order because "the on-chain spike1 golden must not move". | lines 404–414 |
| D5 | **`Annotated`'s license SHRANK to one seam** (M1-E plan review): "`get_type_hints` strips `Annotated` without `include_extras=True` (→ the license SHRINKS to one seam, `decorators._build_record`, storing stripped annotations)". **This is exactly why the `topic` marker is invisible on a method parameter** (§C.9). | lines 500–520 |
| D6 | **The `get` ty-check is TAG-level**, "mirroring the emitter's `abi_check` (Bytes family one tag, **struct↔Map one tag**, element types unchecked)"; and "`storage_key`'s Map branch normalizes ITEMS (keys AND values), and **Struct normalizes identically to its equivalent Map**". | lines 500–520; 561–575 |
| D7 | **Registry discipline is "no renumber, no delete, no meaning reversal" — not "no edits".** Sanctioned edits are enumerated per pass; "a code that later becomes reachable just gets its fixture and leaves the allowlist" (`NO_FIXTURE_ALLOWLIST`). Wording widenings ARE sanctioned when recorded (the SPT3019 row was already widened once this way, `codes.py:592-601`). | lines 182–197, 231–246 |
| D8 | **M1-C E16/E17**: collect-all diagnostics at per-method granularity; **stable `SPT####` codes are public API**. | lines 126–161 |
| D9 | **M1-C E2's ruling on container type bounds**: `Vec`'s `T` was widened to admit `@contracttype` structs because "a decorator cannot add a member that mypy can see" and "(c) would make the flagship token example unwritable". The identical problem recurs for unions/enums (§C.8). | lines 152–161; `types/_ordering.py:44-63` |
| D10 | **M1-C E3 / M1-E D6**: struct storage keys are allowed with an explicit "not modelled in tier 1" ordering note; **`Map` struct VALUES are supported at tier 1, struct KEYS are not**. Container-vs-container ordering is deferred: `Vec._cmp_payload()` raises `NotImplementedError("container comparison; sub-plan B")` (`containers.py:216-217`, `:50-56`). | lines 152–175 |
| D11 | **`compile_module` is the single public compiler entry point**; no expression-level API ships in M1. Every probe in this dossier therefore went through `compile_module`. | lines 248–258 |
| D12 | **The licensed-frontend-edit precedent** (M1-D plan review, reaffirmed in M1-E): "a sub-plan may edit the frontend when its own lowering needs it, with a pinning test, recorded as a ruling." M1-E2 will need this for `decorators.py`, `typemap.py`, `sections.py`, `loader.py`, `recognize.py`, `types_.py`, `ir.py` and `lower.py` — seven layers. | lines 338–380 |

### A.4 Obligations fed INTO M1-E2 (`.superpowers/sdd/2026-08-28-m1e-env-runtime/final-review-attention.md`)

Verbatim, the two parked triage items and the carried line:

> 3. `topic` marker silently inert on a METHOD parameter (Task 5): a
>    refusal wants a sanctioned diagnostic; none exists. Adjudicate: park
>    for the M1-E2/M2 registry pass or demand a fix now.

> 5. SPT3019 / `fits_symbol_small` 9-char topics[0] cap on the CANONICAL
>    spelling vs the 32-char cap on declared events (Task 6 deferred
>    ruling, controller inclination: relax to 32 in a LATER sanctioned
>    pass). LIVE EVIDENCE from Task 8: a realistic event name
>    (RoundClosed) hit the cap and had to be renamed (Tally); the
>    asymmetry is documented in examples/events.py. Confirm parking is
>    still right (it changes published diagnostic behavior — not this
>    branch's remit).

> To M1-E2 (decisions.md 2026-08-31): tagged unions + int enums authoring
> surface — scheduled BEFORE M1's closing deployment; today's pattern is
> per-variant @contracttype keys + Symbol constants.

| ID | Unpacked | Where it is paid |
|---|---|---|
| **X1** | The union + int-enum authoring surface, end to end (decorators → typemap → sections → loader → recognize/expr → IR → emitter → tier-1 model → docs → `must_reject`). | §D, §E1–E9 |
| **X2** | The `topic`-marker refusal: where it lives and what it costs. | §C.9, §E10 |
| **X3** | The SPT3019 relax-to-32 pass: scope and blast radius. | §C.11, §E11 |
| **X4** | Optional rider: the `get`-overload typing gap. | §C.10, §E12 |

### A.5 What the shipped code says, quoted

| ID | Quote (verbatim) | Source |
|---|---|---|
| **Q1** | "`@contracterror` … Each member becomes a generated `ContractError` subclass named for the attribute, carrying `code = N`" — and `errorcode`'s own docstring: "Annotated `-> type[ContractError]` so that `raise Error.NAME` is strict-clean **before the decorator has run**; returns a placeholder that `@contracterror` swaps for the generated exception class." **The placeholder-swap pattern is the template for a variant declaration.** | `decorators.py:143-153, 156-168` |
| **Q2** | The `Event` base's reason to exist: "**A decorator cannot add a member that a type checker can see**, so `publish` lives on a real base class that event types inherit — that is what makes `Transfer(...).publish(env)` type-check under `mypy --strict`. `@contractevent` requires this base." | `env.py:59-72` (M1-E) |
| **Q3** | "`Annotated` is **RE-EXPORTED from `typing`, not redefined**. A contract module may import from `serpent` and nowhere else (the compiler's SPT2005), so the event convention's spelling … would be unauthorable without it." **Any new authoring name M1-E2 needs must join `serpent.__all__` for the same reason.** | `src/serpent/__init__.py:22-27` |
| **Q5** | `build_spec_entries`' contract: "**`types` is not discovered, it is declared** — and so is `events`. … **a caller that omits `types` silently emits a spec whose UDT references have no matching entries**, which decodes fine and renders as an unknown type." | `spec/sections.py:151-166` |
| **Q6** | The pinned entry order: "1. `UDT_STRUCT_V0`, in `types` order, 2. `UDT_ERROR_ENUM_V0`, in `types` order, 3. `FUNCTION_V0`: `__constructor` first, then declaration order, 4. `EVENT_V0`, in `events` order. **Events go LAST on purpose** (ruling E2): appending them cannot move a single byte of a spec that declares none." | `sections.py:167-179` |
| **Q7** | `MakeTopics`' reason to be its own node: "Deliberately its own node, **not a `MakeVec`, because topics are a HETEROGENEOUS chain-value tuple by design** (D8) with `topics[0]` required to be a short `Symbol` naming the event (S11)." **This is precisely a union value's shape.** | `compiler/ir.py:387-395` |
| **Q8** | The emitter's exhaustive-dispatch default: "no lowering for IR node {…}; the expression dispatch is **exhaustive over serpent.compiler.ir by design (F.1.15)** — a new node kind must be added here, **never silently skipped**". | `emitter/lower.py:491-496` |
| **Q9** | `_OBJECT_ABI_TAG`'s own comment: "`Struct` shares `Map`'s tag because a struct **IS** a `Map<Symbol, V>` on chain (S9) — the same ScVal case, not a lookalike." `ABI_CHECKED_TAGS` is DERIVED from the three tables "so a row added to a table here **joins this set automatically** and fails that matrix until it is exercised." | `emitter/lower.py:963-987` |
| **Q10** | `Struct`'s docstring: "**Deliberately NOT a `ChainValue`**: a struct has no `_SCVAL_RANK` and no `_cmp_payload`, so tier 1 cannot ORDER one (A15 forbids inventing an order the host has not been differentially verified against)." | `types/_ordering.py:44-63` |
| **Q11** | `ChainValue`: "This is deliberately a **closed union** rather than `object`: a raw `str` or `int` key is a static error, which is the whole point of the chain types." `ChainValue: TypeAlias = _ChainValue[Any] \| Vec[Any] \| Map[Any, Any] \| Struct`. | `env.py:216-222` |
| **Q12** | `<bucket>.get`'s contract: "`ty` is passed explicitly **because the host returns an untyped `Val`**; it is what tells both the compiler and the type checker what comes back." **The template for a union payload read.** | `env.py:845-851` |
| **Q13** | `Event.publish`'s docstring, on the cap asymmetry: "`Events.publish` refuses a `topics[0]` longer than the 9-character SymbolSmall bound, which is an honest tier-1-only reject for a HAND-WRITTEN topics tuple … A DECLARED prefix topic is capped at the Symbol's 32 characters instead, **on purpose**: `transfer_completed` is an ordinary event name … **Re-running the short-Symbol check here would refuse events the compiler accepts and the chain publishes.**" | `env.py:650-670` |
| **Q14** | `examples/events.py`'s documented asymmetry, verbatim: "**Why `Tally`'s prefix topic is kept short, on purpose.** A DECLARED prefix topic may be up to 32 characters … but the CANONICAL spelling's hand-written topics tuple is held to the stricter 9-character `SymbolSmall` bound at compile time (`SPT3019`) … so its prefix has to satisfy the tighter of the two rules, so `tally` (5 characters) was chosen instead of a longer, more descriptive name that only the authoring form could have carried." | `examples/events.py:22-31` |

### A.6 What M1-E shipped that M1-E2 consumes

| ID | Item | Source |
|---|---|---|
| **P1** | The `@contractevent` five-layer landing is the **worked template** for M1-E2: a new decorator surface, a metadata record extension, a new `SC_SPEC_ENTRY_*` emission, a frontend desugar, zero IR change, zero emitter change. Every one of those seams still exists and is documented. | `decisions.md:404-414`; `decorators.py:261-375`; `sections.py:373-455`; `recognize.py:1294+` |
| **P2** | `serpent.__all__` grew by FOUR sanctioned names in M1-E (`topic`, `Annotated`, `MissingValue`, `AbiCheckFailed`), pinned in `tests/unit/test_public_api.py`. **The precedent for growing it exists and has a cost: one pinned test edit per name.** | attention file, "Public-surface changes" |
| **P3** | `LiteralInventory` fields: `symbols_over_9`, `strings`, `bytes_literals`, `address_strkeys`, `struct_key_descriptor_sets`. **[probe-verified]** a declared `@contractevent(topics=("deposit_completed",))` lands `'deposit_completed'` in `symbols_over_9` with no inventory change — so a >9-char variant name pools for free. | `frontend.py:231-259`; probe 7 |
| **P4** | `SPT8004` ("unsupported", the emitter-coverage code) is in `NO_FIXTURE_ALLOWLIST` and **documented as dormant** because E2's desugar meant no accepted-but-unlowered construct appeared. If M1-E2 adds an IR node the emitter cannot lower, that dormancy ends. | `decisions.md:470-474`; `codes.py:951-963` |
| **P5** | The five examples (`examples/{counter,errors,structs,events,allowance_token}.py`) are in `FIXTURES`, the WAT goldens, the printer's name list, the harness host-fn inventory, the fuzz corpus, `mypy --strict` and `ruff format`. A sixth example joins all seven gates; the inventories **do not fail loudly on a missing one** (attention item 7). | attention file |
| **P6** | The stale-promise gate: `tests/unit/test_no_stale_promises.py` fails on any case-insensitive `"sub-plan e"` in `src/` or `tests/`. **[probe-verified]** `"sub-plan E2"`.lower() contains `"sub-plan e"` → **any docstring saying "sub-plan E2" trips the gate**. Write "M1-E2" instead, or refine the needle. | `test_no_stale_promises.py:32-40, 75-82` |
| **P7** | `docs/subset.md` has a **byte-drift gate** (`tests/unit/test_subset_docs.py`): the checked-in file must equal its generator's output. Every registry or `must_reject` change forces a regeneration in the same commit. | `test_subset_docs.py:27-40` |
| **P8** | `tests/unit/test_bridging_completeness.py` pins its row set against `loader._BRIDGE_RULES` itself, so **a bridge rule added without a row fails loudly**. Any new decorator-time `ValueError` M1-E2 adds needs a bridge rule AND a row. | `test_bridging_completeness.py:1-24` |

---

## B. THE CHAIN TRUTH

### B.1 The on-chain value convention — unit variants are a ONE-ELEMENT Vec, not a bare Symbol

Spec §2 (S2) says "tagged union → `Vec` led by variant-name `Symbol`" and stops
there. §13 says nothing (S11). The unit-variant question is therefore **not
answerable from any in-repo source** and was resolved against upstream Rust.

**[probe-verified against two independent readings of `stellar/rs-soroban-sdk@main`]** —
`soroban-sdk-macros/src/derive_enum.rs`, `map_empty_variant`, the `try_into` arm,
quoted verbatim from the fetched file:

```rust
#enum_ident::#case_ident => {
    let tup: (#path::Val,) = (#path::Symbol::try_from_val(env, &#case_name_str_lit)?.to_val(),);
    tup.try_into_val(env).map_err(Into::into)
}
```

and the XDR arm of the same function:

```rust
let symbol = #path::xdr::ScSymbol(#case_name.try_into()...);
let val = #path::xdr::ScVal::Symbol(symbol);
(val,).try_into()...
```

A **one-element Rust tuple** converts to a one-element `ScVec`. The `try_from`
arm confirms it from the other side: `if iter.len() > 0 { return Err(ConversionError); }`
after the discriminant has been consumed — i.e. the decoder reads a Vec, takes
element 0 as the Symbol, and requires nothing after it. A DeepWiki query on the
same repo returned the same answer independently.

So, pinned:

| Shape | On-chain value | Source |
|---|---|---|
| unit variant `Empty` | `Vec[Symbol("Empty")]` — a **one-element** Vec | `derive_enum.rs::map_empty_variant` [probe-verified] |
| single-payload variant `Circle(U32)` | `Vec[Symbol("Circle"), <u32 val>]` | `derive_enum.rs::map_tuple_variant` [probe-verified] |
| multi-payload variant `Rect(U32, U32)` | `Vec[Symbol("Rect"), v0, v1]` | same |
| **0-element tuple variant** `Circle()` | **UNSUPPORTED in Rust**, verbatim: "Empty tuples are unsupported because it would require extra complexity **to distinguish them from unit-style variants**" → `enum variant {} is unsupported 0-element tuple` | `derive_enum.rs:68-76` [probe-verified] |
| **named-field variant** `Circle { r: u32 }` | **UNSUPPORTED in Rust**: `enum variant {} has unsupported named fields` | `derive_enum.rs:61-67` [probe-verified] |
| int enum `Color::Red = 0` | a bare **`ScVal::U32(discriminant)`** — `try_from_val` is `let discriminant: u32 = val.try_into_val(env)?` | `derive_enum_int.rs:118-125` [probe-verified] |
| int-enum discriminants | **mandatory and explicit**: the macro does `v.discriminant.as_ref().unwrap()` and errors "unsupported discriminant value on enum variant, must be parseable as u32". There is no implicit 0,1,2… | `derive_enum_int.rs:32-50` [probe-verified] |

Two caps that differ from the XDR's, and matter:

* **Tagged-union case names are capped at `SCSYMBOL_LIMIT` (32) by Rust**, not at
  the XDR's 60: `if case_name.len() > SCSYMBOL_LIMIT as usize { … "enum field
  name is too long: {}, max is {}" }` (`derive_enum.rs:51-59`) [probe-verified].
  This is not Rust being conservative — the name **becomes a runtime `Symbol`**
  (§B.1's value table), and S9 makes 32 a hard host limit. A 40-character variant
  name would produce a spec entry that decodes and a value that cannot exist.
* **Int-enum case names carry NO `SCSYMBOL_LIMIT` check** in
  `derive_enum_int.rs` [probe-verified: the constant is not imported there] —
  correctly, because an int-enum case name never becomes a Symbol; the value is
  a u32. So 60 (the XDR cap, `sections.CASE_NAME_LIMIT`) is the right cap there,
  exactly as for `@contracterror`.
* Rust sets `lib: StringM::default()` on the enum entry with the comment "set to
  empty string always because the field is no longer used" — matching
  `sections.py:314`'s existing `lib=b""` for structs.

**What cannot be verified locally, flagged:** nothing above was checked against a
live network or a locally-built Rust artifact. It is **RUST-SOURCE-verified**, a
class weaker than `tests/goldens/README.md`'s two (§C.5) — the plan review should
either build an equivalent `#[contracttype] enum` with `stellar contract build`
and byte-compare the entry (upgrading it to RUST-SDK-BYTE-COMPAT), or record the
gap explicitly. Every §E recommendation that depends on the unit-variant shape is
marked as resting on this.

### B.2 The spec-entry XDR — real instances, constructed and round-tripped

**[probe-verified against `stellar_sdk` 15.0.0]**, exact signatures:

```python
xdr.SCSpecUDTUnionV0(doc: bytes, lib: bytes, name: bytes,
                     cases: list[SCSpecUDTUnionCaseV0])
xdr.SCSpecUDTUnionCaseV0(kind: SCSpecUDTUnionCaseV0Kind,
                         void_case: SCSpecUDTUnionCaseVoidV0 | None = None,
                         tuple_case: SCSpecUDTUnionCaseTupleV0 | None = None)
xdr.SCSpecUDTUnionCaseVoidV0(doc: bytes, name: bytes)
xdr.SCSpecUDTUnionCaseTupleV0(doc: bytes, name: bytes, type: list[SCSpecTypeDef])
xdr.SCSpecUDTEnumV0(doc: bytes, lib: bytes, name: bytes,
                    cases: list[SCSpecUDTEnumCaseV0])
xdr.SCSpecUDTEnumCaseV0(doc: bytes, name: bytes, value: Uint32)

SCSpecUDTUnionCaseV0Kind: SC_SPEC_UDT_UNION_CASE_VOID_V0 = 0 | ..._TUPLE_V0 = 1
SCSpecEntryKind: FUNCTION_V0=0 UDT_STRUCT_V0=1 UDT_UNION_V0=2 UDT_ENUM_V0=3
                 UDT_ERROR_ENUM_V0=4 EVENT_V0=5
```

Caps enforced in `stellar_sdk`'s own constructors [probe-verified by reading each
`__init__`]:

| Field | Cap | Compare |
|---|---|---|
| `SCSpecUDTUnionV0.doc` / every `doc` | `SC_SPEC_DOC_LIMIT` = 1024 | same as struct (`sections.DOC_LIMIT`) |
| `SCSpecUDTUnionV0.lib` / `SCSpecUDTEnumV0.lib` | 80 | serpent always writes `b""` |
| `SCSpecUDTUnionV0.name` / `SCSpecUDTEnumV0.name` | 60 | `sections.TYPE_NAME_LIMIT` = 60 ✔ |
| `SCSpecUDTUnionCaseVoidV0.name` / `…TupleV0.name` / `SCSpecUDTEnumCaseV0.name` | **60** | `sections.CASE_NAME_LIMIT` = 60 ✔ — but see §B.1: unions want **32** |
| `SCSpecUDTUnionV0.cases` / `SCSpecUDTEnumV0.cases` | 2**32−1 | effectively unbounded |
| `SCSpecUDTUnionCaseTupleV0.type` | 2**32−1 | effectively unbounded; Rust's own error text names `VecM::default().max_len()` |

Note by contrast that `SCSpecUDTStructFieldV0.name` is capped at **30**
(`decorators.NAME_LIMIT`) — union/enum case names get 60, struct fields get 30.
That asymmetry already exists for `@contracterror` (`CASE_NAME_LIMIT` vs
`NAME_LIMIT`) and carries over unchanged.

**Two traps `stellar_sdk` does NOT catch** [probe-verified — both accepted and
round-tripped cleanly]:

1. **An empty `cases` list is accepted** for both union and enum. `@contracterror`
   already refuses this at the decorator ("an empty error enum contributes
   nothing to the contract spec", `decorators.py:192-197`); unions and enums need
   the same refusal or they ship a meaningless entry.
2. **A zero-length `SCSpecUDTUnionCaseTupleV0.type` is accepted** — the exact
   shape Rust refuses as "unsupported 0-element tuple" (§B.1). serpent must
   refuse it at declaration time; nothing downstream will.
3. A non-Symbol-charset `name` is accepted (`b"Not-A-Symbol!"` round-tripped).
   serpent's D10 charset ruling already covers this via
   `sections._check_name`/`_check_type_name`.

Constructed instances round-trip byte-exactly: a four-case union entry (one void,
one single-payload, one two-payload, one zero-tuple) is 156 bytes; a three-case
enum entry is 92 bytes; both `SCSpecEntry.from_xdr_bytes(b).to_xdr_bytes() == b`.

### B.3 Where the entries slot, and what the on-chain golden actually constrains

The pinned order is Q6's four groups. **[probe-verified by reading the golden's
own provenance]** the ON-CHAIN anchor is not a stored `.bin`: it is
`test_sections.py` asserting serpent's `build_spec_entries` output is
byte-identical to `spikes/spike1/spike.wasm`'s `contractspecv0`, itself anchored
by `sha256(spike.wasm)` matching the bytes **fetched back off testnet**
(`tests/goldens/README.md:44-60`). That interface is
`setup(counter_limit: U32) -> None`, `bump() -> U32`, one `Settings` struct, one
`LimitExceeded = 7` error case — **no union and no int enum**.

Honest consequence, stated because the E2 ruling's phrasing invites the opposite
reading: **the anchor constrains nothing about placement.** An empty list
contributes zero bytes wherever it sits, so inserting `UDT_UNION_V0` and
`UDT_ENUM_V0` *between* structs and error enums is exactly as byte-safe as
appending them last. D4's "events go last" rationale was true but not
discriminating. The real argument for placement is ecosystem legibility, and the
natural one is the XDR's own kind order (§B.2): structs(1) → unions(2) →
enums(3) → error enums(4) → functions(0) → events(5) — which happens to keep
structs first (anchor-safe by construction) and needs no reordering of anything
that exists.

One thing the anchor *does* constrain: **UDT references are name-only.**
`SCSpecTypeUDT(name=...)` carries a name and nothing else, so structs, unions and
int enums **share one spec namespace** — which means `build_spec_entries`' `seen`
duplicate-name guard (`sections.py:198-210`) must cover the two new kinds, and
`typemap._decorated` must map all three to the same `SC_SPEC_TYPE_UDT`.

---

## C. WHAT EXISTS IN THE REPO TODAY

### C.1 `decorators.py`: the two metadata shapes a union/enum surface would extend

| Kind | Metadata record | Built by |
|---|---|---|
| `@contracttype` | `{"kind": "struct", "fields": [(name, stripped_annotation), ...]}` | `_build_record`, `decorators.py:319-375` |
| `@contracterror` | `{"kind": "error_enum", "cases": [(name, code), ...]}` | `contracterror`, `decorators.py:169-203` |
| `@contractevent` | struct's, plus `"locations"`, `"prefix_topics"`, `"data_format"` | `_build_record` + `decorators.py:361-371` |
| `@contract` | `{"kind": "contract", "methods": [(name, params, returns), ...]}` | `contract`, `decorators.py:621-651` |

**`@contracterror` is the closest per-member precedent** and it is worth being
precise about *why*: `errorcode(N)` returns an `_ErrorCode` placeholder
(`decorators.py:124-153`) that the decorator replaces with a generated
`ContractError` subclass (`_make_error_class`, `:222-233`), and its declared
return type is `type[ContractError]` **only so the raise site is strict-clean
before the decorator runs** (Q1). Spec S3 records that this was arrived at by
live repro and that "no decorator typing trick rescues" the bare-int form. A
union variant declaration has the same shape of problem and needs the same shape
of answer — but a harder one, because a variant constructor's return type is the
*enclosing class*, which does not exist while the body executes. §C.8 probes it.

### C.2 The "documented workaround" — a correction

D1 names the workaround "per-variant `@contracttype` keys + Symbol constants
(token_style's shape)". What `tests/fixtures/token_style.py` actually does
[verified by reading all 145 lines]:

* module-level `Symbol` constants as *storage keys* — `ADMIN = Symbol("ADMIN")`,
  `NAME_KEY = Symbol("NAME")` (`:63-64`);
* one `@contracttype` struct used as a *composite storage key* —
  `BalanceKey(owner=...)` (`:73-78`), documented as "the dominant real-world
  pattern".

That is **storage-key discrimination, not a tagged-union value**. Nothing in
`tests/fixtures/`, `examples/`, `sandbox/` or `tests/semantics/` builds or reads
a discriminated value of any kind. So:

* the workaround D1 describes is real and useful, but it is a *keyspace*
  workaround — an author who wants a `Shape` that is *either* a circle *or* a
  rectangle has no documented pattern at all today;
* there is **no in-tree fixture to graduate**, unlike the events case where
  `token_style` already had a publish line (M1-E §B.5.3). Every union/enum
  fixture and example M1-E2 needs is net-new;
* the honest statement of the M1 gap is therefore stronger than D1's phrasing
  suggests, which argues for M1-E2 rather than against it.

### C.3 `spec/typemap.py` + `spec/sections.py`: what changes

`typemap._decorated` (`:237-258`) maps `kind == "struct"` → `SC_SPEC_TYPE_UDT` by
class name, and refuses `error_enum`/`event`/`contract` with per-kind
explanations from `_REFUSED_KINDS` (`:117-131`). Adding unions and int enums is
**two rows in the mappable branch**, both producing the same
`SCSpecTypeDef(SC_SPEC_TYPE_UDT, udt=SCSpecTypeUDT(name=...))` — because a UDT
reference is name-only (§B.3). `_udt_name`'s 60-byte cap (`:260-279`) applies
unchanged.

`sections.build_spec_entries` (`:151-222`) needs: two new arms in
`_declared_type_entry` (`:225-249`), two new `_union_entry`/`_int_enum_entry`
builders beside `_struct_entry` (`:300-325`) and `_enum_entry` (`:328-352`) — the
latter is *already* the exact template for an int enum, differing only in the
entry kind and the case-name cap — and two new buckets in the entry-order
assembly (`:195-222`). `_check_name`/`_check_type_name`/`_doc_bytes` are reused
verbatim; per-case `doc` follows the existing `b""` gap (B13).

Note the `_declared_type_entry` return is `tuple[str, xdr.SCSpecEntry]` with the
caller doing `(structs if kind == "struct" else enums).append(entry)` — a
two-way branch that four kinds will not fit. The smallest honest shape is a
`dict[str, list[...]]` keyed by kind, ordered by an explicit tuple.

### C.4 The loader: what a new decorator costs

`loader._DECORATOR_KINDS` (`:268-273`) maps four `serpent.__all__` names to four
kinds. A new decorator needs a row there, a row in `_ALLOWED_MEMBER_FORMS`
(`:767-772`) and `_BODY_HELP` (`:774-782`), and — because the member forms drive
the F.1.14 metadata↔AST cross-check — a `_cross_check_*` arm beside
`_cross_check_fields`/`_cross_check_cases` (`:1255-1295`).

The body form for a union under the surface §D proposes is `assign` (`Empty =
variant()`), the same form `error_enum` already declares, so
`_check_class_body`'s machinery needs no new concept. Base classes: `allowed_bases
= 1 if kind == "event" else 0` (`:739`) — a union (and, per §C.8, an int enum)
needs the same allowance.

Import restriction: `_check_import_from` (`:594-670`) admits only names in
`serpent.__all__` under SPT2005, and `_decorator_serpent_name` (`:680-696`)
already accepts **both** the bare-`ast.Name` and the `ast.Call` decorator
spellings (a licensed M1-E deviation, ratified retroactively per the 2026-08-31
rulings) — so a `@contractunion(...)` factory form, if ever wanted, is already
parseable.

### C.5 The IR and the emitter: is new IR avoidable? — the central architecture probe

D4's precedent is "desugar to existing IR, the emitter changes not at all". Two
probes decide whether it holds here.

**Probe A — a union value's shape is already lowerable.** Compiling

```python
env.events().publish((Symbol("Deposit"), n), n)   # n: U32, a runtime value
```

**[probe-verified]** produces
`HostCall("contract_event", (MakeTopics(topics=(Const(ty=Symbol, py_value='Deposit'), ParamRef(ty=U32))), ParamRef(ty=U32)))`
and builds a 348-byte module importing exactly
`{contract_event, fail_with_error, vec_new, vec_push_back}`.

Reading `_lower_make_vec` (`emitter/lower.py:1495-1523`) confirms *why*: it takes
`(items, all_static)` and **never looks at any element type** — the linear-memory
form when every item is a `Const`, otherwise `vec_new` + a rebound
`vec_push_back` per item. `MakeTopics` is dispatched to it with one line
(`lower.py:484-489`). **A union value — a heterogeneous Vec led by a Symbol
`Const` — is byte-for-byte the shape the emitter already builds.**

**Probe B — `MakeVec` cannot honestly carry it.** `MakeVec` has a single
`elem_ty` (`ir.py:345-363`). **[probe-verified]** the only readers of `elem_ty`
outside `ir.py` are the frontend's own constructors (`recognize.py:1413-1414,
1838-1839`) and `stmt.py:971-1007`'s `for x in vec` desugar, which reads
`iterable.ty.elem` to type the induction `vec_get`. So a heterogeneous `MakeVec`
would lower *correctly* today and be a **lie** in the IR — one that
`for x in some_union` would consume as truth. Q7 records that this is exactly why
`MakeTopics` exists as its own node.

**Conclusion, and it is the answer to the "probably THE central architecture
question":** the *lowering* needs nothing new; the *IR* needs one node.
`MakeUnion(union_name, case, payload: tuple[IRExpr, ...])` is a `MakeTopics`
twin — one dataclass, one dispatch line delegating to `_lower_make_vec` with the
Symbol `Const` prepended, one `Ty` tag. Q8's exhaustive-dispatch default and P4's
dormant `SPT8004` mean the alternative ("reuse and hope") fails loudly rather
than silently, but it fails at the wrong layer.

Int enums need **nothing**: an enum value is `Const(ty=<enum ty>, py_value=N)`,
and `_lower_const` already emits a `U32` immediate for a U32-repr `Const`
(`lower.py:500-510`). Zero IR nodes, zero emitter lines.

**`Ty` has no UNION or ENUM tag** (`types_.py:82-107`: 19 real tags + INVALID).
Two new tags cost, by inspection of Q9's derived-set discipline:
`_OBJECT_ABI_TAG[TyTag.UNION] = val.TAG_VEC_OBJECT` and
`_IMMEDIATE_ABI_WORD[TyTag.ENUM] = val.TAG_U32` — **one row each**, after which
`ABI_CHECKED_TAGS` picks them up automatically and
`test_emitter_lower_stmts.py`'s accept/reject matrix fails until exercised. That
is the cheapest possible integration, and it is cheap *because* M1-E built the
derived-set machinery.

`StructDecl`/`ErrorEnumDecl`/`EventDecl` (`ir.py:555-593`) gain two siblings;
`decls.ModuleDecls` (`decls.py:178-180`) gains two lists; `SpecInputs`
(`frontend.py:262-285`) needs unions and int enums in
`declared_types_in_order` — they are declared TYPES a UDT reference can name,
unlike an event (Q5/MJ-9), so they travel in `types=`, not a third keyword.

### C.6 Consumption: what compiles today, probed

Every row **[probe-verified]** through `compile_module`:

| Source | Result |
|---|---|
| `match s: case _: ...` | `SPT1024` — "structural pattern matching is not supported: `Match` is not part of the serpent subset", **help: "use an if/elif chain"** |
| `isinstance(s, Symbol)` | `SPT1017` — "this builtin is not supported: `isinstance` is a python builtin with no on-chain equivalent" |
| `if s.tag == Symbol("Circle"): return s.a` on a `@contracttype` with `tag: Symbol` | **COMPILES** |
| `if v.get(U32(0)) == Symbol("Empty"):` on a `Vec[Symbol]` | **COMPILES** |
| `if c == RED:` against a module-level `RED = U32(0)` | **COMPILES** |
| `x: U32 \| None` parameter, `if x is None:` | `SPT1012` — "identity has no on-chain meaning; use ==" |
| same, `if x == None:` | **COMPILES** — and does **not narrow**: the following `return x` is still `SPT3018` "returns U32, but this value is Option(U32)" |
| same, `if x:` | `SPT3015` — "truthiness is only defined for numeric chain types and Bool … Option(U32) has no truthiness on chain" |
| `-> U32 \| None` returning `None`; `Option` passed straight through | **COMPILES** |
| `@contracttype` with `circle: U32 \| None` fields, reading `s.circle` as `U32` | `SPT3018` |

Three findings that shape §D:

1. **The `if/elif` chain over a Symbol comparison is the only branching form the
   subset has, and `SPT1024`'s own help text already recommends it.** A union
   read pattern built on `tag() == Symbol("Case")` is not a workaround — it is
   the shape the compiler already tells authors to write.
2. **`Option` has no narrowing whatsoever.** `is None` is refused, `== None`
   compiles but narrows nothing, truthiness is refused, and there is no `unwrap`.
   This **kills the otherwise-attractive "union as an all-Optional
   `dataclass_transform` record"** design (`Shape(circle=U32(3))`, read via
   `if s.circle is not None`) — it would be mypy-clean and completely
   uncompilable. Recording this saves the plan a wrong turn.
3. Struct field read + `Symbol` `==` already lower to `map_get` + `obj_cmp`, so
   the *ingredients* of a union read exist; only the surface is missing.

### C.7 Storage keys, equality and the tier-1 families

* `types/_storage_key.py` is the ONE cross-tier key definition. A struct
  normalizes **identically to its equivalent field-keyed Map** —
  `("map", frozenset((storage_key(Symbol(field)), storage_key(value)) …))`
  (`_struct_key`, `:91-108`) — and a `Vec` normalizes to
  `("vec", tuple(storage_key(el) …))`, order-sensitively (`:80-82`).
  **[probe-verified]** `storage_key(Vec(U32, [U32(1)])) == ('vec', ((3, 1),))`.
  A union value whose tier-1 form holds a `Vec` therefore gets its storage-key
  normalization **for free and correctly** — the Vec branch already produces
  exactly the on-chain shape. An int enum whose tier-1 form carries
  `_SCVAL_RANK`/`_cmp_payload` like a `U32` gets `(3, N)`, also for free.
* `env._FAMILY_BY_TYPE` (`env.py:271-287`) is the tag-level `get` ty-check (D6),
  restated from the emitter's `abi_check` tables and **pinned to them in both
  directions** by
  `test_env_model.py::test_the_tag_families_agree_with_the_emitters_abi_check_tables`.
  A union's family is `"vec"`, an int enum's is `"u32"`. Consequence, and it is
  the exact coarseness D6 already accepted for struct↔Map: **`get(k, Shape)`
  will accept a stored plain `Vec`, and `get(k, Color)` a stored plain `U32`.**
  Additive, symmetric with what shipped, and honest only if documented.
* `tag_of_chain_value` (`env.py:301-315`) walks the MRO against
  `_FAMILY_BY_TYPE`, then falls through to `isinstance(value, Struct)`.
  **`Struct` is a `runtime_checkable` Protocol matching `__dataclass_fields__`
  (`types/_ordering.py:44-61`)** — so **if a union or enum instance is
  implemented as a dataclass it silently satisfies `Struct` and is classified as
  a Map.** That is a real trap: it would give a union the wrong family, the
  wrong storage key, and the wrong ABI tag, all without an error. Either the new
  kinds are not dataclasses, or every `Struct` test site is reordered to ask
  about unions/enums first. Flagged again as F.1.
* Ordering: `Vec._cmp_payload()` raises
  `NotImplementedError("container comparison; sub-plan B")`
  (`containers.py:216-217`), so two `Vec`s of equal rank cannot be ordered at
  tier 1. **[probe-verified]** `Map(Vec, U32, [(v, U32(1))])` with ONE key
  succeeds (a one-element binary search compares nothing); a second key would
  raise. A union inherits that hole exactly — usable as a *storage* key
  (hash-based `storage_key`), unusable as a multi-entry `Map` key. Same shape as
  D10's struct-key note, and it should get the same "not modelled in tier 1"
  wording rather than a new invention (Q10/A15).
* Deep-copy: `set()` does `copy.deepcopy(value)` and `get()` returns
  `copy.deepcopy(stored)` (`env.py:902, 923`), and events/auth-args copy too
  (`:1196, 1386`). E5's isolation property covers "every `ChainValue` shape"; a
  new value kind joins that property, and if it is immutable (§D) the property
  is trivially satisfied.

### C.8 `mypy --strict`: the static-visibility problem, solved by probe

This is the hardest constraint and the one the `@contracterror` precedent (S3,
Q1) exists to warn about. Nine candidate surfaces were type-checked under
`uv run mypy --strict`. Results, all **[probe-verified]**:

| Candidate | Verdict |
|---|---|
| `Empty = variant()` / `Circle = variant(U32)` with `def variant(*p: type) -> Any` | **FAILS.** `--strict` includes `--warn-return-any`: `return Shape.Circle(U32(3))` from a `-> Shape` function is `error: Returning Any from function declared to return "Shape" [no-any-return]`. Any `-> Any` factory is dead. |
| annotation-only variants (`Empty: None`, `Circle: U32`) | **FAILS.** `Shape.Circle(U32(3))` → `error: "U32" not callable [operator]`. |
| author-written forward-ref annotation (`Empty: "Shape" = unit()`) | works but is verbose and still needs a `Callable[...]` spelling for payload variants — neither `Callable` nor `ClassVar` is in `serpent.__all__` (Q3). |
| all-`Optional` `dataclass_transform` record | mypy-clean and **uncompilable** — no Option narrowing exists (§C.6 finding 2). |
| **descriptor-typed variants** — a `__get__(self, obj, owner: type[_O]) -> _O` for unit and `-> Callable[[_P], _O]` for payload, with the descriptor `Generic[_P]` | **CLEAN.** Zero errors on the surface module; `Shape.Empty` types as `Shape`, `Shape.Circle(U32(3))` types as `Shape`, `Color.Red` types as `Color`. |

The descriptor form's static strength was then probed for author mistakes:

| Author mistake | Caught? |
|---|---|
| `Shape.Circle(Symbol("nope"))` with `Circle = variant(U32)` | **YES** — `Argument 1 has incompatible type "Symbol"; expected "U32" [arg-type]` — but **only** once the descriptor is `Generic[_P]` and the factory is `variant(p: type[_P]) -> _Variant1[_P]`. With a non-generic descriptor the payload type is silently unchecked. |
| `Shape.Rect(U32(1))` on a two-payload variant | **YES** — `Too few arguments [call-arg]` |
| `Shape.Circle(U32(1), U32(2))` on a one-payload variant | **YES** — `Too many arguments [call-arg]` |
| `Shape.Empty()` (calling a unit variant) | **YES** — `"Shape" not callable [operator]` |
| returning a `Shape` where a `Color` is declared | **YES** — `Incompatible return value type` |

A three-way `@overload` on a single `variant` name (`() -> _Unit`,
`(type[_P]) -> _Variant1[_P]`, `(type[_P], type[_Q]) -> _Variant2[_P, _Q]`) is
**[probe-verified]** clean with no `overload-cannot-match` warnings, and a full
worked surface — overloaded `variant`, a `tag() -> Symbol` base method, and a
`payload(index: U32, ty: type[_T]) -> _T` base method — type-checked with **zero
errors** except the one deliberately-wrong line.

**And one further probe that decides whether a base class is optional.** `Struct`
matches on `__dataclass_fields__`; `ChainValue` is a *closed* static union (Q11).
A `@contractenum` class with **no** base gives, at every `ChainValue` position:

```
error: Argument 2 to "store" has incompatible type "ColorNoBase";
       expected "_ChainValue | Struct"  [arg-type]
```

and the same error against a `ChainValue` widened only with a base the class does
not inherit. Inheriting a serpent-exported base and widening the alias by that
one arm is **clean**. So: *both* new kinds need a real, exported base class, for
exactly Q2's reason and exactly D9's reason — a decorator cannot make mypy see
membership. That is one or two new `serpent.__all__` names (P2's cost) and one
new arm in `ChainValue`.

### C.9 Fed item 1 — the `topic` marker outside an event field

The mechanism, verified end to end. `_build_record` reads annotations with
`include_extras=True` and is **the one seam** where `Annotated` survives (D5,
`decorators.py:328-340`, quoted: "Without that flag `get_type_hints` silently
STRIPS `Annotated`, the `topic` marker vanishes before anything can see it").
`contract._check_method` reads `_annotations_of(func)` **without** the flag
(`decorators.py:683`). Therefore a method parameter's marker is gone before any
check could see it.

**[probe-verified]** current behaviour, five positions:

| Position | Today |
|---|---|
| `def go(self, env: Env, x: Annotated[U32, topic]) -> U32` | **COMPILES SILENTLY** — the marker is inert |
| `def go(self, env: Env) -> Annotated[U32, topic]` | **COMPILES SILENTLY** — a second inert position the triage did not name |
| `@contracttype` field `a: Annotated[U32, topic]` | refused, but as **`SPT1037`** — MJ-11's catch-all, message "this construct is not supported by the serpent subset", with the decorator's precise sentence demoted to a `note`. The decorator's own message ("`topic` marks a field of a @contractevent class as a published topic; a @contracttype struct has no topics, so the marker would be silently ignored here", `decorators.py:347-352`) **has no `_BRIDGE_RULES` row** (`loader.py:314-347`) |
| function-body `x: Annotated[U32, topic] = U32(1)` | `SPT3013` — accurate ("this annotation cannot be expressed in the contract spec") but not the real reason |
| module-level `K: Annotated[U32, topic] = U32(1)` | `SPT1031` — refused as a top-level statement, unrelated to the marker |

So the refusal is **one `ValueError` in `_check_method` plus one bridge rule**,
and the honest scope is *four* positions, not one: the two silent ones plus a
correct code for the struct-field case (which today gets a wrong-family message —
the same mismatch `codes.py:580-591` and `:749-768` already record as the reason
SPT3019 and SPT4019/SPT4020 were added). Cost, itemised: read the method
annotation with `include_extras=True` and split it; one new registry row (or a
sanctioned widening); one `_BRIDGE_RULES` row; one
`test_bridging_completeness.py` row (P8 makes this mandatory, not optional); one
`must_reject` fixture; one `docs/subset.md` regeneration (P7). No emitter, no IR,
no tier-1 change.

### C.10 Fed item 2 — the SPT3019 relax-to-32 pass

The asymmetry, quoted from both sides: `Event.publish` deliberately skips the
short-Symbol check because "**Re-running the short-Symbol check here would refuse
events the compiler accepts and the chain publishes**" (Q13), while the canonical
spelling's hand-written tuple is held to nine characters. Q14 is the live
evidence in shipped prose: `examples/events.py` documents that `Tally` was named
for the tighter rule.

**The exact scope of a relax-to-32 pass**, enumerated from the tree:

| # | Site | Change |
|---|---|---|
| 1 | `recognize.py:1270-1277` — the `_is_short_symbol(first)` branch raising SPT3019 with "topics[0] is too long; event topic Symbols must be <= 9 characters" | the length arm becomes the `SCSYMBOL_LIMIT` 32 check (or is dropped, since `Symbol("a"*33)` is already a reject — T1) |
| 2 | `env.py:1177-1180` — `Events.publish`'s `val.fits_symbol_small(name.text)` → `BadArgument` | same relaxation, or removal |
| 3 | `codes.py:574-579` — SPT3019's `construct`/`message_intent` ("topics[0] is not a short Symbol") | a **sanctioned wording widening** under D7, which the row has already had once (`codes.py:592-601`) |
| 4 | `recognize.py:248` — the help string "make topics[0] a short Symbol, e.g. Symbol('transfer')" | reworded |
| 5 | `env.py:650-670` and `env.py:1152-1160` — two docstrings whose whole subject is the asymmetry (Q13) | rewritten; the asymmetry disappears |
| 6 | `examples/events.py:22-31` — Q14's ten-line explanation, plus (optionally) renaming `Tally` back to something descriptive | prose; a rename moves the WAT golden, the IR golden, the spec entry and `test_examples.py` |
| 7 | `tests/must_reject/types/event_topic_not_symbol.py` | **stays** — it tests the *not-a-Symbol* arm, not the length arm. A length-arm fixture does not exist today; if the length arm survives at 32, one is needed |
| 8 | `tests/unit/test_recognize_env.py:476` (`_assert_reject(diag, "SPT3019", "too long")`) and `tests/unit/test_env_model.py:731` (the tier-1 twin) | both flip to assert the new bound |
| 9 | `tests/unit/test_decorators.py:444, 480` — two tests whose docstrings *explain* the asymmetry | rewritten |
| 10 | `docs/subset.md:1543-1563` | regenerated (P7's byte-drift gate fails until it is) |

**Blast radius, honestly:** SPT3019 keeps its number and one of its two arms
(not-a-Symbol), so D7's "no meaning reversal" is respected — the code's meaning
narrows, and accepts strictly grow. The published diagnostic behaviour does
change, which is why M1-E parked it. The pass is ~10 sites, entirely
frontend/tier-1/docs, and touches no IR, no emitter and no spec entry.

### C.11 Optional rider — the `get` overload gap

**[probe-verified]** with real `serpent`, `mypy --strict`:

```python
return env.storage().instance().get(U32(1), U32, default=0)
# error: Incompatible return value type (got "object", expected "U32")  [return-value]
```

Correction to the brief: the diagnostic is **`[return-value]`, not `[arg-type]`** —
mypy solves `_T` from *both* `ty: type[_T]` and `default: _T | None`
(`env.py:845`) and joins to `object`. So the workaround an author needs today is
`# type: ignore[return-value]` at the call, or an intermediate annotated local.
Every shipped example spells `default=U32(0)` (`examples/allowance_token.py:206`
and six more, `examples/errors.py:105`), which is why nothing in the tree carries
the ignore — the gap is invisible to CI and visible to authors.

**[probe-verified]** a three-`@overload` `get` fixes it, with the raw-literal
overload **first** so `_T` is solved from `ty` alone:

```python
@overload
def get(self, key: ChainValue, ty: type[_T], *, default: int | str | bytes | bool) -> _T: ...
@overload
def get(self, key: ChainValue, ty: type[_T], *, default: _T) -> _T: ...
@overload
def get(self, key: ChainValue, ty: type[_T]) -> _T: ...
```

Under that shape: `default=0` clean, `default=U32(0)` clean, no default clean, a
mismatched chain-value default still an error, and a mismatched *return* still an
error. Ordering matters — with the `_T` overload first, `default=0` still joins to
`object`. One caveat: making `default` keyword-only in the overloads narrows
accepts (today `get(k, ty, d)` is positional); either add a positional overload
or record the narrowing.

### C.12 The SPT registry and the gates a new surface must join

`codes.py` is 100 rows across eight bands (**[probe-verified]** by importing
`REGISTRY`), band maxima: **SPT1039, SPT2006, SPT3020, SPT4020, SPT5005,
SPT6001, SPT7005, SPT8004**. `NO_FIXTURE_ALLOWLIST` holds nine codes.
`codes.validate()` enforces uniqueness, band-prefix correctness and non-empty
owning task; `diagnostics.Diagnostics.error` refuses an unregistered code.

**Codes M1-E2 likely needs — enumerated, not numbered** (append after each band's
maximum; D7 forbids renumbering, not appending):

* SPT1xxx: reaching a union or enum *class* in a value position where the
  construct is not supported (the `SPT1037`-avoidance pattern of D7's precedent);
  a union/enum surface reached in a position M1 does not lower.
* SPT3xxx: a union/enum-typed value used where a chain type is required and vice
  versa (SPT3018 may widen instead); a `payload()` index out of the declared
  variant's arity; a `payload()` `ty` disagreeing with the declared payload type;
  a `tag()` comparison against a Symbol that names no variant (a genuinely useful
  diagnostic — a typo'd case name would otherwise be a dead branch).
* SPT4xxx: an empty `@contractunion`/`@contractenum` body (the
  `@contracterror`-empty precedent, SPT4011); a zero-element tuple variant
  (§B.2 trap 2); a duplicate variant name; a duplicate or out-of-range int-enum
  discriminant (SPT4009/SPT4010's twins); a missing int-enum discriminant
  (§B.1 — Rust makes them mandatory); a `@contractunion` class missing its base
  (SPT4014's twin).
* SPT5xxx: a union variant name over **32** characters (§B.1) — either a new row
  or a **sanctioned widening of SPT5003**, whose `construct` today reads
  "`@contracterror` case name — length > 60 or non-Symbol charset"; an int-enum
  case name over 60 fits SPT5003's existing meaning with a wording widening.
* One row (or a widening) for §C.9's `topic`-marker refusal.

Other gates a new declaration kind joins: `tests/unit/test_public_api.py`'s
`__all__` pin (P2); `docs/subset.md`'s "Every top-level class needs exactly one of
@contract/@contracttype/@contracterror/@contractevent" sentence
(`docs/subset.md:28`, plus three more occurrences at `:1888, 1940, 1959`) and its
byte-drift gate (P7); `tests/unit/test_bridging_completeness.py` (P8);
`test_no_stale_promises.py` (P6 — do not write "sub-plan E2" in `src`/`tests`);
`FIXTURES` + WAT goldens + printer names + harness host-fn inventory + fuzz
corpus if an example is added (P5); `tests/semantics/env_scenarios.py` +
`tests/fixtures/env_surface.py` if the tier-1 model gains observable behaviour.

---

## D. THE PROPOSED SURFACE (the smallest thing that makes S1 true)

Offered as a proposal; §E is where each half is ruled on. Every line below was
type-checked or compiled in §B/§C.

**Declaration.** Two decorators, two exported bases, two factories:

```python
from serpent import (Address, Env, Symbol, U32, contract, contractenum,
                     contractunion, enumvalue, variant, ContractEnum, ContractUnion)

@contractunion
class Shape(ContractUnion):
    """A shape."""
    Empty = variant()                # unit    -> Vec[Symbol("Empty")]
    Circle = variant(U32)            # 1 field -> Vec[Symbol("Circle"), r]
    Rect = variant(U32, U32)         # n fields -> Vec[Symbol("Rect"), w, h]

@contractenum
class Color(ContractEnum):
    """A color."""
    Red = enumvalue(0)               # -> U32(0)
    Green = enumvalue(1)
```

**Construction.** `Shape.Empty` (a value), `Shape.Circle(U32(3))`,
`Shape.Rect(U32(1), U32(2))`, `Color.Red` — all typed as their owning class by
the descriptor `__get__(self, obj, owner: type[_O]) -> _O` trick, **strict-clean
with payload-type and arity checking** (§C.8).

**Consumption.** Two methods on `ContractUnion`, mirroring Q12's
"`ty` is passed explicitly because the host returns an untyped `Val`":

```python
def area(self, env: Env, s: Shape) -> U32:
    if s.tag() == Symbol("Circle"):
        return s.payload(U32(0), U32)          # -> vec_get(s, 1)
    if s.tag() == Symbol("Rect"):
        return s.payload(U32(0), U32) * s.payload(U32(1), U32)
    return U32(0)
```

`tag()` → `vec_front` (`v.8`) or `vec_get(s, 0)`; `payload(i, ty)` →
`vec_get(s, i+1)` plus the tag-level narrow check the storage `get` already
emits. Int enums consume by `==`, which already compiles for UDT values
(**[probe-verified]** struct `==` compiles). Both patterns are exactly the
`if/elif` chain `SPT1024`'s own help text recommends (§C.6).

**Scope fence — what is M1 and what defers, with the named workaround:**

| Shape | M1-E2 | Deferral + workaround |
|---|---|---|
| unit variant | **IN** | — |
| single-payload variant | **IN** | — |
| multi-payload tuple variant | **IN** (arity cap: propose 12, mirroring S4's tuple-arity cap; additive to relax) | — |
| 0-element tuple variant | **OUT** — refused at declaration (Rust refuses it too, §B.1) | write a unit variant; permanent |
| named-field variant | **OUT** — refused (Rust refuses it too) | a single-payload variant carrying a `@contracttype` struct |
| int enum with explicit discriminants | **IN** | — |
| int enum with implicit discriminants | **OUT** — mandatory `enumvalue(N)`, mirroring both Rust and `errorcode(N)` | spell the numbers |
| `match` sugar over a union | **OUT (M2)** | the `if/elif` chain over `tag()`; `SPT1024`'s help already says so |
| generic / parameterized unions | **OUT (M2)** | one concrete union per instantiation |
| a union as a multi-entry `Map` key | **OUT** — "not modelled in tier 1", D10's exact wording | a `@contracttype` key struct, or one entry |
| cross-contract union arguments | **OUT (M2)** — cross-contract itself is M2 (S12) | — |
| Option narrowing (would enable other union spellings) | **OUT (M2)** | `== None` compiles but does not narrow (§C.6) |

Every restriction above is **additive to relax**: accepts only grow when any of
them is lifted, which is the property D1's "reversal cost" line depends on.

---

## E. OPEN QUESTIONS FOR THE CONTROLLER

Thirteen. Each has options and **one** recommendation with evidence. The dossier
recommends; the controller rules.

---

**E1 — The union declaration + construction surface (the biggest question).**

Options: **(a)** descriptor-typed `variant()` factories in the class body with an
exported base (§D); **(b)** annotation-only variants (`Circle: U32`); **(c)** an
`Any`-returning factory; **(d)** an all-`Optional` `dataclass_transform` record
(`Shape(circle=U32(3))`); **(e)** author-written forward-ref annotations
(`Empty: "Shape" = unit()`); **(f)** defer unions, ship only int enums, and amend
the spec.

**Recommendation: (a).** It is the only option that is **[probe-verified]**
strict-clean *and* compilable. (c) fails `--warn-return-any` at every author call
site (§C.8) — and `--strict` cleanliness is the SDK's headline claim (S3, Q2).
(b) gives `"U32" not callable`. (d) is mypy-clean and **uncompilable**: the
subset has no Option narrowing at all (§C.6, five probes) — accepting it would
ship an authoring surface whose read side does not exist. (e) works but leans on
`Callable`/`ClassVar`, neither in `serpent.__all__` (Q3), and puts the union's
own name in the annotation three times. (f) contradicts S1 and D2, and D2 already
recorded that the alternative to shipping is *amending the spec*.

Costs of (a), stated plainly: `serpent.__all__` grows by up to five names
(`contractunion`, `contractenum`, `variant`, `enumvalue`, and one or two bases),
each a `test_public_api.py` edit (P2); the descriptor must be `Generic[_P]` or
payload types go unchecked by mypy (§C.8, probed both ways); and the `variant`
overload set needs one arm per supported arity, so the arity cap is a real
surface decision, not just a checker constant.

---

**E2 — The union READ surface.**

Options: **(a)** `tag() -> Symbol` + `payload(index: U32, ty: type[_T]) -> _T` on
the base (§D); **(b)** per-variant accessors the decorator generates (not
statically visible — Q2 forbids); **(c)** read the value as a raw `Vec` and let
the author call `vec_get` (no type safety, and `Vec`'s element type is
homogeneous); **(d)** no read surface in M1 — unions are write/pass-through only.

**Recommendation: (a).** It is **[probe-verified]** strict-clean; it reuses Q12's
established "pass `ty` explicitly because the host returns an untyped `Val`"
convention verbatim, so authors learn one rule not two; it lowers to `vec_front`
/ `vec_get` plus the same tag-level check `storage.get` already emits (D6), i.e.
zero new host functions; and the resulting `if/elif` over `tag() ==
Symbol("Case")` is literally the rewrite `SPT1024` already recommends (§C.6).
(b) cannot type-check. (c) has no static story and would make `Vec`'s `elem_ty`
a lie (§C.5 probe B). **(d) is the honest cheap fallback and should be named as
such**: a union that can be constructed, stored, returned and passed but not
destructured is still enough to make S1's sentence true, and it removes the
`payload()` index/arity/type diagnostics entirely. Against it: a union you cannot
read is not a union anyone will use, and the second sub-plan would have to
revisit the base class — a *breaking* change after docs, which is the specific
cost D4's reversal note warns about.

Two sub-decisions inside (a): whether `payload`'s index is 0-based over the
*payload* (proposed) or over the underlying Vec; and whether an
out-of-arity/wrong-`ty` `payload()` call is a compile error (it can be — the
variant is not statically known at the call, but the *union's* full arity range
and per-slot type set are, so a slot index above the maximum arity and a `ty`
matching no variant's slot are both statically decidable).

---

**E3 — Does M1-E2 add IR nodes, and how much emitter change?**

Options: **(a)** one new `MakeUnion` node, dispatched to the existing
`_lower_make_vec`; **(b)** reuse `MakeVec` with a synthetic element type;
**(c)** reuse `MakeTopics`; **(d)** desugar in the frontend to
`MakeVec`/`HostCall` chains with no new node at all.

**Recommendation: (a).** §C.5 probe A shows the *lowering* already exists —
`_lower_make_vec` never reads an element type, and a heterogeneous
Symbol-led vec builds today (348 bytes, `vec_new` + `vec_push_back`). §C.5 probe
B shows `MakeVec` cannot honestly carry it: `elem_ty` is read by
`stmt.py:971-1007`'s `for x in vec` desugar, so a lying `elem_ty` would be
consumed as truth — which is exactly Q7's recorded reason `MakeTopics` is its own
node. (c) would overload a node whose docstring pins it to event topics and whose
`ty` is `Ty.Void` by design (`recognize.py:1284-1290`). (d) is (b) by another
name.

So D4's precedent holds in the form that matters — **the emitter changes by one
dispatch line and two ABI-table rows** (§C.5), and `ABI_CHECKED_TAGS`'
derived-set discipline (Q9) makes those rows self-policing. Int enums need
**nothing**: `Const(ty=<enum>, py_value=N)` rides `_lower_const`'s U32 path.
State in the plan that P4's dormant `SPT8004` stays dormant only if the node is
added to `lower.py`'s dispatch in the same task that adds it to `ir.py`.

---

**E4 — New `Ty` tags, or reuse `VEC`/`U32`?**

Options: **(a)** `TyTag.UNION` and `TyTag.ENUM`, each with a `name`; **(b)** reuse
`TyTag.VEC`/`TyTag.U32` and carry the UDT name in `Ty.name`; **(c)** `UNION` only,
enums as `U32`.

**Recommendation: (a).** The checker must distinguish a union from a `Vec` to
reject `for x in shape` and to type `payload()`, and an enum from a `U32` to
reject `Color.Red + U32(1)` and to resolve `Color` as a UDT reference. (b) makes
both distinctions unavailable exactly where they are needed. (c) is (b) for
enums. The cost of (a) is bounded and knowable: one `_OBJECT_ABI_TAG` row
(`UNION → TAG_VEC_OBJECT`), one `_IMMEDIATE_ABI_WORD` row (`ENUM → TAG_U32`), and
whatever `Ty.render()`/`repr_form` arms the two tags need — after which Q9's
derived set and its accept/reject matrix carry the enforcement.

Note the spec type is **not** affected: `to_spec_type` reads the *annotation*, not
`Ty` (§C.3), so both tags map to `SC_SPEC_TYPE_UDT` independently.

---

**E5 — The int-enum surface: base class, discriminants, and what `==` means.**

Options for declaration: **(a)** `Red = enumvalue(0)` with mandatory explicit
discriminants (mirrors both Rust, §B.1, and `errorcode(N)`, Q1); **(b)** implicit
0,1,2… from declaration order; **(c)** `Red: U32 = U32(0)` annotated values.

Options for the base: **(1)** an exported `ContractEnum` base; **(2)** no base,
with the decorator adding `_SCVAL_RANK`/`_cmp_payload` at runtime; **(3)** make
the enum a subclass of `U32`.

**Recommendation: (a) + (1).** (a) because Rust makes the discriminant mandatory
and unwraps on its absence (`derive_enum_int.rs:32-50`, [probe-verified]) — an
implicit numbering is an on-chain-visible value serpent would be *inventing*, and
reordering the class body would silently change stored data; and because
`errorcode(N)` set exactly this precedent for exactly this reason (S3). (1)
because **[probe-verified]** a base-less enum value is not statically a
`ChainValue` — `error: incompatible type "ColorNoBase"; expected "_ChainValue |
Struct"` — so (2) produces a surface that cannot be stored, published or compared
under `mypy --strict`; this is D9's ruling recurring verbatim ("a decorator
cannot add a member that mypy can see"). (3) breaks
`typemap._SCALARS`' exact-class match (B8: "an author subclass of `U32` is
unmappable") and would make `Color` arithmetic accidentally legal.

Sub-decision: does `Color` expose its number? Recommend **no** in M1 —
`.value`-style introspection is tier-1-only (the M1-C §B.2 line rejecting
`.value`/`.text`/`.data` on chain types), and an author who needs the number can
declare a `U32` constant. Additive to relax.

---

**E6 — Which union shapes are M1?**

Options: **(a)** unit + single-payload + multi-payload tuple (up to a cap);
**(b)** unit + single-payload only; **(c)** unit only.

**Recommendation: (a), with the arity cap set at 12** — S4's own tuple-arity
number, so serpent has one arity story rather than two, and one `variant`
overload per arity up to it. Rationale for going past (b): the two-address
allowance key `(from, spender)` that `env.py:78-83`'s storage-key docstring names
is the canonical multi-payload shape, and the incremental cost over (b) is
`variant`'s overload list plus one loop in the emitter path that already handles
n items. Rationale for the cap at all: `variant`'s overloads are written by hand,
and an unbounded arity would need a `*payload: type` fallback returning
something unchecked — which is candidate (c) of §C.8, the one that fails
`--warn-return-any`.

Refuse, at declaration, with named codes: a **0-element tuple variant** and a
**named-field variant** — both because Rust refuses them (§B.1) and the first
because it is representationally indistinguishable from a unit variant, which is
Rust's own stated reason. **Refuse an empty union body**, mirroring SPT4011.

---

**E7 — Spec entry order and the `types=` inventory.**

Options: **(a)** structs → **unions → int enums** → error enums → functions →
events (the XDR's own kind order, §B.2); **(b)** append unions and enums after
events, mirroring D4's "append so nothing moves"; **(c)** one flat UDT group in
`types` declaration order, then error enums, then functions, then events.

**Recommendation: (a).** §B.3 establishes — and this corrects a natural
misreading of D4 — that **the on-chain anchor constrains nothing here**: it
declares no union and no int enum, and an empty list contributes zero bytes at
any position. So placement is a legibility decision, and the XDR kind order is
the only non-arbitrary one available. (b) would put UDT entries after the event
entries that reference nothing, which reads backwards. (c) is defensible and
cheaper (one list, not three) but loses the by-kind grouping the existing
docstring (Q6) promises and tests independently of the goldens.

Also ruled here: unions and int enums travel in **`types=`**, not a new keyword —
they are declared TYPES a UDT reference can name (unlike an event, MJ-9), and Q5's
"a caller that omits `types` silently emits a spec whose UDT references have no
matching entries" applies to them identically. Which means `build_spec_entries`'
`seen` duplicate-name guard (`sections.py:198-210`) and `SpecInputs`'
`declared_types_in_order` both cover them with no structural change.

---

**E8 — Name caps: 32 or 60 for a union variant?**

Options: **(a)** 32 for union variant names, 60 for int-enum case names;
**(b)** 60 for both (the XDR cap); **(c)** 30 for both (`NAME_LIMIT`, matching
struct fields).

**Recommendation: (a).** A union variant name **becomes a runtime `Symbol`**
(§B.1's value table), and S9 makes `SCSYMBOL_LIMIT` = 32 a hard host limit — a
40-character variant name yields a spec entry that decodes and a value that
cannot be constructed. Rust caps it at exactly `SCSYMBOL_LIMIT`
(`derive_enum.rs:51-59`, [probe-verified]). An int-enum case name never becomes a
Symbol (the value is a u32), so 60 is right there and matches `@contracterror`'s
`CASE_NAME_LIMIT` exactly. (b) ships an unconstructible value class. (c) is
gratuitously stricter than both the XDR and Rust.

Registry consequence: either a new SPT5xxx row for "union variant name > 32", or
a **sanctioned wording widening of SPT5003** (today "@contracterror case name")
to cover every UDT case name with a per-kind limit in the message. D7 permits the
widening; the row has precedent for it. **Recommend the widening**, so the code
count does not grow for a check that is genuinely the same check.

---

**E9 — The tier-1 representation, and the `Struct`-Protocol collision.**

Options: **(a)** a union instance holds a `Vec[Any]` internally, is immutable, and
is NOT a dataclass; **(b)** a union instance is a frozen dataclass carrying
`(tag, payload)`; **(c)** a union instance is a thin wrapper with no container.

**Recommendation: (a), and it is partly a bug-avoidance ruling.** §C.7's probe:
`Struct` is a `runtime_checkable` Protocol matching `__dataclass_fields__`, and
`tag_of_chain_value`, `_families_of_ty` and `storage_key` all fall through to it.
**A dataclass-based union or enum would silently be classified as a Map** — wrong
family, wrong storage key, wrong ABI tag, no error anywhere. So (b) is not merely
less good, it is a trap; if the controller wants (b) anyway, every `Struct` test
site must be reordered to ask about the new kinds first, and that reordering needs
its own pinning test.

(a) also buys three things free: `storage_key`'s `Vec` branch already produces
exactly the on-chain normalization (**[probe-verified]**
`storage_key(Vec(U32,[U32(1)])) == ('vec', ((3,1),))`); immutability makes E5's
deep-copy isolation property trivially true for the new kind; and the tier-1
`tag()`/`payload()` implementations are two lines over the held `Vec`.

Record explicitly, in the model's own docstring and in the docs: a union is
usable as a **storage** key (hash-based `storage_key`) but **not orderable at
tier 1** — `Vec._cmp_payload()` raises `NotImplementedError("container
comparison; sub-plan B")` (`containers.py:216-217`), so a multi-entry
`Map[Shape, V]` cannot be ordered. **[probe-verified]** a one-key
`Map(Vec, U32, ...)` succeeds because a one-element binary search compares
nothing — which is a *worse* failure mode than an outright refusal, because it
means the reject appears only on the second insert. Use D10's exact "not modelled
in tier 1" wording; do not invent an order (Q10/A15).

An int-enum instance carries `_SCVAL_RANK = 3` and a `_cmp_payload` of its
discriminant, i.e. it is orderable and hashable exactly like a `U32`, and needs
none of the above caveats.

---

**E10 — The `topic`-marker refusal (fed item, X2/D3).**

Options: **(a)** refuse at decorator time with a `ValueError` + a bridge rule,
covering **all four** positions §C.9 found (method parameter, method return,
struct field re-coded off `SPT1037`, and a function-body/module-level annotation);
**(b)** refuse the two silent positions only; **(c)** refuse nothing and document
the marker as event-fields-only; **(d)** make the marker *meaningful* on a method
parameter (an auto-published event) — a new feature.

**Recommendation: (a), scoped to the two silent positions plus a correct code for
the struct field.** The silent ones are the actual defect: **[probe-verified]** a
method parameter and a method return both compile clean with an inert marker,
which is precisely the "silently ignored" failure the decorator's own struct-field
message already calls out as unacceptable (`decorators.py:347-352`). The
struct-field case is already refused but under `SPT1037`'s "not supported by the
serpent subset", the same wrong-family message that D7's own precedent
(`codes.py:580-591`, `:749-768`) records as the reason SPT3019 and
SPT4019/SPT4020 were created — so re-coding it costs one bridge rule and buys a
correct message. Leave the function-body case on `SPT3013`, which is accurate
enough and would need annotation-resolver plumbing to improve.

Cost, itemised (§C.9): read `_check_method`'s annotations with
`include_extras=True` and split; one registry row (or widening); one
`_BRIDGE_RULES` row; one `test_bridging_completeness.py` row — **mandatory**,
because P8's meta-test pins the row set against `_BRIDGE_RULES` itself and a rule
without a row fails loudly; one `must_reject` fixture; one `docs/subset.md`
regeneration. No IR, no emitter, no tier-1 change. Against (d): it is a feature
nobody asked for, and S13's "scope creep toward real Python" names exactly this.

One caution the plan must carry: turning on `include_extras=True` in
`_check_method` **widens what that function sees**, and D5 recorded that the
`Annotated` license was deliberately *shrunk* to one seam. This is a second seam.
It should be spelled as such, with a note that the stripped annotation is still
what flows into the metadata — so `to_spec_type` and `resolve_annotation` remain
`Annotated`-unaware, which is the property D5 was protecting.

---

**E11 — The SPT3019 relax-to-32 pass (fed item, X3/D3).**

Options: **(a)** relax the length arm to 32 (`SCSYMBOL_LIMIT`); **(b)** drop the
length arm entirely and let `Symbol`'s own 32-char reject (T1) carry it;
**(c)** keep 9 and instead *tighten* declared prefix topics to 9 for symmetry;
**(d)** keep the parking — defer to M2.

**Recommendation: (b), which is (a) done honestly.** The controller's standing
inclination is to relax (D3), and the evidence for relaxing is now in shipped
prose in two places: Q13's "re-running the short-Symbol check here would refuse
events the compiler accepts and the chain publishes" and Q14's account of
`RoundClosed` → `Tally`. Between (a) and (b): the 32-char bound is *already*
enforced, twice — `Symbol("a"*33)` is a frozen semantics reject case (T1) and
`Symbol.__init__` raises — so a second 32-char check inside SPT3019's length arm
would be dead code. Dropping the arm leaves SPT3019 meaning exactly one thing
("topics[0] is not a Symbol"), which is a *narrowing* of a public code's meaning,
not a reversal — D7-compliant, and accepts strictly grow.

Scope and blast radius are enumerated at §C.10: ten sites, all
frontend/tier-1/docs, no IR, no emitter, no spec entry. Two riders: (i) the
existing `must_reject` fixture tests the *not-a-Symbol* arm and stays; there is
**no** length-arm fixture today, so under (b) nothing needs to be moved, and
under (a) one must be written; (ii) whether to rename `Tally` back to something
descriptive is a separate call — it would move the WAT golden, the IR golden, the
spec entry and `test_examples.py`, and it is the kind of churn better done as its
own commit inside the same pass. Against (c): it would refuse
`transfer_completed`, which Q13 calls "an ordinary event name". Against (d): the
asymmetry is now *documented to users* in a shipped example (Q14), which is the
point at which a known-wrong rule stops being cheap to fix.

---

**E12 — The `get`-overload rider (X4).**

Options: **(a)** the three-overload `get` §C.11 probed; **(b)** change `default`'s
annotation to `object`, losing the chain-value default's type check;
**(c)** leave it and document the `# type: ignore[return-value]`.

**Recommendation: (a).** **[probe-verified]** it fixes `default=0` while keeping
`default=U32(0)` clean, a mismatched chain-value default an error, and a
mismatched return an error — and the overload ORDER is load-bearing (raw-literal
arm first, or `_T` still joins to `object`). It is ~6 lines in `env.py` plus a
`mypy --strict` pin. (b) is one line and silently accepts
`get(k, U32, default=Symbol("x"))`. (c) is defensible only because no shipped
example hits the gap — every one spells `default=U32(0)` — but the 2026-08-31
`get`-adoption ruling exists *precisely* to make `default=0` a first-class
spelling at runtime, so leaving it un-typeable is now internally inconsistent.

One caveat to record: today `get(k, ty, d)` accepts a positional default; the
proposed overloads make it keyword-only. Either add a positional overload or
record the narrowing explicitly (accepts shrink, which is the direction that
needs a reason).

---

**E13 — The scope fence, and how much net-new fixture/example surface.**

§D's table is the proposal. What the controller ratifies is the fence and the
test/example footprint, because §C.2's correction changes the estimate: **there
is no in-tree union asset to graduate.** Options: **(a)** a sixth
`examples/unions.py` (or `examples/shapes.py`) joining all seven gates (P5), plus
`must_reject` fixtures per new code, plus a tier-1 model + differential rows;
**(b)** fixtures and `must_reject` only, no new example, with the union/enum
surface documented in `examples/structs.py`; **(c)** (a) plus extending
`tests/fixtures/env_surface.py` and `tests/semantics/env_scenarios.py` so the
new value kind rides the E9 stateful differential.

**Recommendation: (a) + the `env_surface`/`env_scenarios` half of (c).** An
example, because R6 puts M1-E2 *before* the testnet deploy and S1's sentence is
what M1 is judged against — a scope line satisfied only by unit tests is
satisfied on paper. The differential rows, because §C.7 shows a union's storage
round-trip exercises the `Vec` branch of `storage_key` and the `"vec"` tag family,
neither of which any existing scenario covers with a UDT, and because the E9 table
is explicitly the corpus F re-runs on tier 2b (D-era ruling) — a value kind absent
from it is a value kind tier 2b never checks. Against a full sixth example:
P5 records that the six inventories **do not fail loudly on a missing entry**, so
adding one means editing seven lists by hand; the plan should treat that as a
named task, not a cleanup.

Explicitly *out*, with the workaround named in §D's table: `match` sugar,
generics, named-field variants, 0-element tuple variants, implicit discriminants,
multi-entry `Map` keys, cross-contract, Option narrowing.

---

## F. RISKS

### F.1 Where M1-E2 can silently diverge

Ordered by likelihood × silence. Every failure mode below is "tests pass, docs
read well, the contract behaves differently on chain".

| # | Divergence | Why it is silent | Mitigation |
|---|---|---|---|
| **1** | **A unit variant emitted as a bare `Symbol` instead of a one-element `Vec`.** §B.1 is RUST-SOURCE-verified only — no live network, no locally built Rust artifact. | Both forms are valid `ScVal`s; a serpent-only round-trip passes either way. The consumer that discovers it is a Rust client's `try_from_val`, which would `ConversionError` — **on chain, months later**. | Build an equivalent `#[contracttype] enum` with `stellar contract build` and byte-compare BOTH the spec entry and a constructed value, upgrading §B.1 to RUST-SDK-BYTE-COMPAT. If that is not achievable, record the gap in `tests/goldens/README.md`'s own voice and carry it to F. **The plan review must re-probe this.** |
| **2** | **A dataclass-based union or enum silently classified as a `Map`.** `Struct` matches `__dataclass_fields__` and is the fallthrough in `tag_of_chain_value`, `_families_of_ty` and `storage_key` (§C.7). | Every path answers *something* plausible: the family is "map", the storage key is a field-keyed map, the ABI tag is `TAG_MAP_OBJECT`. A scalar-only test never notices. | §E9's (a): not a dataclass. If (b) is chosen, reorder every `Struct` test site and pin the ordering. |
| **3** | **A heterogeneous `MakeVec` with a lying `elem_ty`.** The emitter ignores `elem_ty` (§C.5 probe A), so the module builds and runs; `stmt.py:971-1007`'s `for x in vec` desugar reads it as truth. | The lie is invisible until someone writes `for x in shape`, at which point the induction `vec_get` is typed as the union's first payload type. | §E3's dedicated node. Add a frontend assertion that `MakeVec.elem_ty` types every item. |
| **4** | **A variant name over 32 characters.** The XDR accepts 60 [probe-verified]; `stellar_sdk` accepts it; the spec entry decodes. | The *value* is unconstructible, but only at the construction site — and if no test constructs that variant, nothing fails. | §E8's 32-char cap, enforced at declaration with a source-located code, plus a `must_reject` fixture at exactly 33. |
| **5** | **An empty union/enum body, or a 0-element tuple variant.** Both **[probe-verified] accepted by `stellar_sdk`** and round-trip cleanly. | A meaningless-but-valid entry ships; ecosystem tools render an empty type. Rust's own macro refuses both, so a Rust consumer diverges from a Python producer. | Refuse both at declaration (§E6), with the `@contracterror`-empty precedent's wording. |
| **6** | **A union or enum class omitted from `SpecInputs.declared_types_in_order`.** Q5: "a caller that omits `types` silently emits a spec whose UDT references have no matching entries, which decodes fine and renders as an unknown type." | The same silence Q5 already names, now for two more kinds. | Extend the existing test that a contract mentioning a struct gets a matching entry, in both directions, for unions and enums. |
| **7** | **A `tag()` comparison against a Symbol naming no variant.** `if s.tag() == Symbol("Cirlce")` compiles today (§C.6 proves `Symbol` `==` compiles) and is a permanently dead branch. | The contract compiles, deploys and quietly takes the fallthrough forever. | A dedicated SPT3xxx code (§C.12): the union's variant name set is statically known at the comparison. This is the single highest-value *new* diagnostic in the surface. |
| **8** | **A multi-entry `Map` keyed on a union.** **[probe-verified]** one key succeeds; the second raises `NotImplementedError`. | The failure appears only on the second insert, so a one-entry test is green. | D10's "not modelled in tier 1" wording in the model, the docs and a `must_reject`-adjacent test with two keys. Do not invent a container order (Q10/A15). |
| **9** | **The tier-1 model and the emitter disagreeing on the `payload()` narrow check.** D6 made the storage `get` check tag-level and pinned it to the emitter's tables in both directions. A second `ty`-taking read is a second place to get that wrong. | A tier-1 test passes because the model *is* the assertion. | Reuse `env._require_ty` verbatim for `payload()`; extend `test_env_model.py`'s two-direction agreement test to cover it. |
| **10** | **`docs/subset.md` and the registry drifting.** Any new code, any reworded row, any moved fixture. | P7's byte-drift gate fails loudly — which is the *good* case. The silent case is a code registered with no fixture and no allowlist entry. | Schedule the regeneration as an explicit step per D7/S6, not a cleanup; keep the completeness meta-test in both directions. |
| **11** | **`"sub-plan E2"` in a `src/` or `tests/` docstring.** **[probe-verified]** it contains the case-insensitive needle `"sub-plan e"` and trips `test_no_stale_promises.py`. | It fails loudly, but at an unrelated-looking test, and the temptation is to allowlist rather than reword. | Write "M1-E2" in code; if the phrase is genuinely needed, refine the needle rather than growing the allowlist. |
| **12** | **The `Annotated` seam widening.** §E10 turns on `include_extras=True` in a second place, after D5 deliberately shrank the license to one. | Nothing fails; the risk is that a later reader assumes annotations in the metadata may carry `Annotated`. | Keep the *stored* annotation stripped everywhere; say so in `_check_method`'s docstring; pin that `resolve_annotation` and `to_spec_type` never see an `Annotated`. |
| **13** | **SPT3019's relaxation reaching a diagnostic users already followed.** Q14's shipped prose tells authors to keep event names short. | Nothing breaks — accepts grow — but the docs now lie in the other direction. | Rewrite Q13/Q14's prose in the same pass; do not relax the checker and leave the explanation. |

### F.2 Checks that belong in M1-E2's own test plan

1. **Value-shape goldens**: for each of unit / single-payload / multi-payload, the
   compiled WAT and the tier-1 value, asserted to be a Vec of exactly
   `1 + arity` elements led by the variant Symbol (F.1.1's local half).
2. **A RUST-SDK-BYTE-COMPAT golden** for `SCSpecUDTUnionV0` and
   `SCSpecUDTEnumV0` against a Rust `#[contracttype] enum` artifact, if one can
   be built — the only independent check available (F.1.1).
3. **XDR cap negative controls**: an over-long variant name, an empty body, a
   0-element tuple variant, a non-Symbol-charset name — each a source-located
   serpent error, **never a bare `stellar_sdk` `ValueError`** (R3).
4. **Entry-order test** independent of the goldens, extended to six groups (Q6's
   existing pattern), plus the byte-identity assertion for the on-chain spike1
   anchor re-run unchanged (§B.3).
5. **The two-direction ABI-table agreement test** extended to the new tags
   (`test_env_model.py`'s existing one, plus
   `test_emitter_lower_stmts.py`'s matrix, which Q9 makes self-policing).
6. **A `mypy --strict` fixture** for the whole authoring surface — the
   `token_style.py` role (A23) for unions: every construction spelling, every
   read, plus a `# type: ignore`-free file. Plus negative type-check assertions
   for the four mistakes §C.8 probed as caught.
7. **Storage round-trip** of a union and an enum through all three durabilities,
   including a struct-keyed and a union-keyed entry, in
   `tests/fixtures/env_surface.py` + `tests/semantics/env_scenarios.py` (§E13).
8. **Deep-copy isolation** for the new kinds (E5's property, extended).
9. **`must_reject` fixtures** for every new code, with the completeness meta-test
   in both directions; `docs/subset.md` regenerated and its drift gate green.
10. **The `topic`-refusal bridging row** in `test_bridging_completeness.py` (P8
    makes this non-optional) and a `must_reject` fixture per refused position.
11. **The SPT3019 pass**: both remaining arms pinned, the tier-1 twin
    (`test_env_model.py:731`) flipped with it, and a test that the surviving
    32-char bound is enforced by `Symbol` itself rather than restated.
12. **The `get` overloads** pinned by a `mypy --strict` fixture covering
    `default=0`, `default=U32(0)`, no default, and the two mismatch cases.

### F.3 Process risks

* **Seven layers, one sub-plan.** D1's own sizing note names five; §C finds seven
  (`decorators`, `typemap`, `sections`, `loader`, `recognize`/`decls`/`types_`,
  `ir`, `lower`) plus `env.py`, `__init__.py`, `codes.py`, `docs/subset.md` and
  the example/fixture inventories. D12's licensed-edit precedent covers it, but
  each edit wants its own pinning test and its own ledger line.
* **This is the last sub-plan before the deploy gate.** R6/D1: M1-E2 lands before
  a user-approved testnet deployment that is the one hard stop in the autonomy
  grant. Anything left unproven in an example is deployed unproven.
* **The authoring surface is breaking-after-docs.** D4's reversal note said the
  event convention was "cheap now, breaking after docs/examples" — and E ships
  the docs and examples. The same is true here, with less slack: after M1-E2 there
  is no third sub-plan to defer to, only M2 and a spec amendment (D2).
* **`serpent.__all__` grows again.** M1-E's four names were each a deliberate
  sanction (P2). Five more is a larger single step than any prior sub-plan took;
  the plan should justify each name individually, or fold `variant`/`enumvalue`
  into one factory if the overloads permit.
* **`env.py` is already at ~1,550 lines** and D3 records that this fires E10's
  package-promotion trigger, deferred to M2. §E9 and §E12 both add to it. If the
  union/enum model pushes it materially further, the promotion argument
  strengthens — but it is M2's, not M1-E2's, and the plan should say so rather
  than drift into it.
* **`decisions.md` is controller-owned.** M1-E2 records rulings in its own SDD
  ledger; the controller promotes the lasting ones — the pattern every prior
  sub-plan followed.
* **The one fact this dossier could not verify locally** is §B.1's unit-variant
  representation and the tuple-variant payload ordering: both are
  RUST-SOURCE-verified from two independent readings, neither is
  network-confirmed. §E1, §E2, §E3, §E6 and §E8 all rest on it. **The plan review
  must re-probe it**, ideally by building a Rust artifact and byte-comparing.

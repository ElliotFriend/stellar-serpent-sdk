# M1-B: Host Interface Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The machine-readable host interface: a pinned `env.json`, code-generated
bindings for all 199 host functions with protocol gates, the computed-protocol-floor
logic, and the sections module (contractenvmetav0 / contractspecv0 / contractmetav0)
built from decorator metadata via `stellar_sdk` XDR — with golden-byte proof against
Phase 0's on-chain-verified artifacts.

**Architecture:** Everything here is data and pure functions — no compiler, no
runtime. `serpent/_host/` holds the pinned `env.json`, the codegen script, and the
CHECKED-IN generated bindings module (a drift test proves regeneration is
byte-identical). `serpent/spec/` builds the three custom sections from
`_serpent_type_` metadata + docstrings. Consumers: sub-plan C reads the bindings to
recognize API calls; D imports them to emit WASM imports and attaches the sections;
F's harness binds implementations by the same table.

**Tech Stack:** Python ≥ 3.11, stellar-sdk ≥ 15 (NOW a runtime dependency of the
`spec` module — decision recorded below), pytest, Hypothesis, mypy --strict, ruff.

**Spec:** `docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md`
(§4 compiler pipeline, §7 custom sections, §13 appendix). Findings:
`docs/superpowers/specs/2026-08-26-phase0-findings.md`. Decisions:
`docs/superpowers/decisions.md`.

## Global Constraints

- Pinned `env.json`: rs-soroban-env tag **v28.0.2** — byte-identical to the upstream
  blob (Phase 0 verified git blob SHA `f9c50fc25c8f32cdc0a6d6f465d3b14143d446e3`;
  assert it in a test via `git hash-object`-equivalent hashing in Python: blob SHA =
  sha1(b"blob " + str(len(data)).encode() + b"\x00" + data)).
- Exactly **199 functions across 11 modules** (x10 i52 m14 v19 l21 d2 b26 c37 a12 t2
  p4) — counts pinned by test.
- Bindings are LOOKED UP BY NAME; export codes are data, never hardcoded elsewhere.
- Raw-scalar (non-Val) interface types: `RAW_SCALAR_TYPES` = {u64, i64, u32, i32,
  StorageType, ContractTtlExtension} is a SUPERSET of what v28.0.2 uses (u32/i32
  are declared interface types with zero occurrences in this pin — tests assert
  MEMBERSHIP, never occurrence). The full observed arg-type vocabulary also
  includes plain `Symbol`, `Error`, `Bool`, `Void` and the *Object/*Val forms —
  the bindings derive per-arg Val-typedness and wasm types from an EXPLICIT,
  exhaustive type table whose key set must exactly equal the type set observed in
  env.json (unknown type at re-pin = hard failure naming it).
- Computed protocol floor: `max(min_supported_protocol of used fns, default 20)`;
  emitting an import gated above the build target is an error naming the function;
  declared protocol may be raised, never lowered below the floor. Target default 27.
- Sections via `stellar_sdk.xdr` classes exclusively (no hand-rolled XDR); doc/lib/
  name fields are BYTES; spec name caps enforced with source-located errors
  (function/field ≤ 30, type names ≤ 60, docs ≤ 1024; tuple spec types are
  deferred — no authoring surface).
- Golden constants, correctly attributed: `env_meta(27)` == hex
  `000000000000001b00000000` (12 bytes) — ON-CHAIN-verified (Phase 0). The
  get/increment counter spec payload (64 bytes) is RUST-SDK-BYTE-COMPAT-verified
  (Phase 0 landscape spike compared it `==` against a `stellar contract build`
  artifact); its generator is `spikes/spike1/reference/mkmeta.py` (which hardcodes
  exactly get/increment) — regenerate from that recorded logic and pin as a
  regression golden labeled "Rust-SDK-compat". A SECOND, on-chain-anchored check:
  build spec entries for spike1's REAL interface (setup(counter_limit: U32) ->
  None, bump() -> U32, Settings struct, Error enum LimitExceeded=7) and assert
  `stellar contract info interface --wasm spikes/spike1/spike.wasm` renders
  identically to serpent's rendering of the same entries (that contract IS
  on-chain-verified; its render is recorded byte-for-byte in DEPLOY_LOG.md).
  tests/goldens/README.md states which golden is which class.
- Type mapping (chain type → SCSpecTypeDef): Bool→BOOL(1), U32→U32(4), I32→I32(5),
  U64→U64(6), I64→I64(7), Timepoint→TIMEPOINT(8), Duration→DURATION(9),
  U128→U128(10), I128→I128(11), Bytes (`_LENGTH is None`)→BYTES(14), any Bytes
  subclass with `_LENGTH == n` (Bytes32/Bytes64/bytes_n(n) alike — key off
  _LENGTH, never a whitelist)→BYTES_N(n), String→STRING(16), Symbol→SYMBOL(17),
  Address→ADDRESS(19), Vec[T]→VEC(1002), Map[K,V]→MAP(1004), `X | None`→
  OPTION(1000), @contracttype→UDT(2000 by name), None return→empty outputs list.
  Integer values shown FOR REVIEW ONLY — code uses `xdr.SCSpecType.SC_SPEC_TYPE_*`
  symbolically, never literals (spec §13's numeric table is Val TAGS, which
  coincide with SCSpecType on 4-13 and diverge at 1 and 14 — a cross-reading
  trap). U256/I256 (M2-deferred), MuxedAddress, Val, Result, Tuple have no
  authoring surface: all unmappable → SpecTypeError.
- All gates green at every commit: full suite, `mypy --strict`, `ruff check .`,
  `ruff format --check src tests`.
- Commits: conventional, no emojis, explicit paths, Co-Authored-By trailer:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`; signing fallback
  (try once ~40s; on failure `--no-gpg-sign` + append to `.git/unsigned-commits.log`).
- Spikes remain frozen (copy values/bytes out, never import spike modules).
- Do not touch `docs/superpowers/decisions.md` (controller-owned).

**Recorded decision (controller):** `stellar-sdk` moves from dev-only to a runtime
dependency of the NEW `serpent.spec` subpackage only — the zero-dep rule holds for
`serpent` core (val/types/errors/decorators/env must keep importing nothing
external; a test enforces this by walking their import graphs). Rationale: spec §7
mandates stellar_sdk XDR classes for section emission; build-time-only usage keeps
authored contracts dependency-free at runtime. Declared as
`[project.optional-dependencies] spec = ["stellar-sdk>=15,<16"]` — an extra, so
`pip install serpent` stays zero-dep; the dev group already carries it for tests.

## File Structure

```
src/serpent/_host/
├── __init__.py          # re-exports only (defines nothing): HostFn, HOST_FUNCTIONS,
│                        #   functions_by_name, RAW_SCALAR_TYPES, scalar constants,
│                        #   compute_protocol_floor, check_protocol_target, declared_protocol
├── _model.py            # HostFn dataclass + RAW_SCALAR_TYPES + the exhaustive
│                        #   env-type -> wasm-type table (breaks the __init__/bindings cycle;
│                        #   bindings.py imports ONLY stdlib + serpent._host._model)
├── _scalars.py          # STORAGE_TYPE = {"temporary": 0, "persistent": 1, "instance": 2}
│                        #   (source: spec §13) + CONTRACT_TTL_EXTENSION (values sourced from
│                        #   rs-soroban-env v28.0.2 at implementation time — if unsourceable,
│                        #   defer the constant with a named TODO; never let sub-plan D invent it)
├── env.json             # pinned v28.0.2, byte-identical to upstream blob
├── _codegen.py          # env.json -> bindings.py generator; pipes rendered source
│                        #   through `ruff format -` before writing so generated output is
│                        #   format-stable BY CONSTRUCTION (reconciles the format gate with
│                        #   the byte-identical drift test); supports --out PATH (default:
│                        #   the checked-in path)
└── bindings.py          # GENERATED, checked in; drift-tested against regeneration
src/serpent/spec/
├── __init__.py          # build_env_meta, build_spec_entries, build_meta
├── typemap.py           # chain type / metadata annotation -> SCSpecTypeDef
└── sections.py          # the three section builders + validation errors
tests/unit/test_host_bindings.py, test_protocol_floor.py,
tests/unit/test_typemap.py, test_sections.py, test_core_zero_dep.py
tests/goldens/           # counter_spec.bin (64B), env_meta_27.bin (12B) + README
```

---

### Task 1: Pinned env.json + codegen + generated bindings

**Files:**
- Create: `src/serpent/_host/__init__.py`, `src/serpent/_host/env.json` (copied
  from `spikes/spike1/env.json` — verify byte-identity to the spike copy AND the
  upstream blob SHA), `src/serpent/_host/_codegen.py`, `src/serpent/_host/bindings.py`
  (generated), `tests/goldens/README.md`
- Test: `tests/unit/test_host_bindings.py`

**Interfaces:**
- `_model.py`: `@dataclass(frozen=True) class HostFn: name: str; module: str;
  export: str; arity: int; arg_names: tuple[str, ...]; arg_types: tuple[str, ...];
  ret_type: str; min_protocol: int | None; max_protocol: int | None; docs: str`
  (docs defaults to "" — six fns in this pin lack the key: put/has/get/
  del_contract_data, compute_hash_sha256, verify_sig_ed25519; codegen renders via
  repr(); pin docstrings are ASCII, quote-free, max 843 chars — assert in codegen).
  Computed `@property`s (NOT emitted fields — bindings.py emits declared fields
  only): `val_typed_args: tuple[bool, ...]`, `wasm_params: tuple[str, ...]` and
  `wasm_result: str` ("i64"/"i32", from the exhaustive type table). A test asserts
  every v28.0.2 entry is all-i64 — promoting the spike's uniform-i64 assumption to
  an asserted invariant.
- `bindings.py`: `HOST_FUNCTIONS: tuple[HostFn, ...]` (all 199, declaration order).
- `__init__.py`: re-exports + `functions_by_name: dict[str, HostFn]`.
- `_scalars.py` constants per File Structure.
- `_codegen.py`: runnable `uv run python -m serpent._host._codegen [--out PATH]`
  (default writes the checked-in path), deterministic, ruff-format-piped output,
  "GENERATED — do not edit" header naming the pin.

- [ ] **Step 1: Write the failing tests**

```python
import hashlib
import pathlib
import subprocess
import sys

from serpent._host import HOST_FUNCTIONS, RAW_SCALAR_TYPES, functions_by_name

ENV_JSON = pathlib.Path(__file__).parents[2] / "src" / "serpent" / "_host" / "env.json"
UPSTREAM_BLOB_SHA = "f9c50fc25c8f32cdc0a6d6f465d3b14143d446e3"


def test_env_json_matches_upstream_blob() -> None:
    data = ENV_JSON.read_bytes()
    blob = b"blob " + str(len(data)).encode() + b"\x00" + data
    assert hashlib.sha1(blob).hexdigest() == UPSTREAM_BLOB_SHA


def test_function_counts_by_module() -> None:
    assert len(HOST_FUNCTIONS) == 199
    counts: dict[str, int] = {}
    for fn in HOST_FUNCTIONS:
        counts[fn.module] = counts.get(fn.module, 0) + 1
    assert counts == {"x": 10, "i": 52, "m": 14, "v": 19, "l": 21,
                      "d": 2, "b": 26, "c": 37, "a": 12, "t": 2, "p": 4}


def test_known_exports_resolved_by_name() -> None:
    # Verified in Phase 0 against the live network
    assert (functions_by_name["put_contract_data"].module,
            functions_by_name["put_contract_data"].export) == ("l", "_")
    assert (functions_by_name["map_new_from_linear_memory"].module,
            functions_by_name["map_new_from_linear_memory"].export) == ("m", "9")
    assert (functions_by_name["fail_with_error"].module,
            functions_by_name["fail_with_error"].export) == ("x", "5")
    assert (functions_by_name["symbol_new_from_linear_memory"].module,
            functions_by_name["symbol_new_from_linear_memory"].export) == ("b", "j")


def test_raw_scalar_args_distinguished() -> None:
    put = functions_by_name["put_contract_data"]
    # (key Val, value Val, StorageType raw scalar)
    assert put.val_typed_args == (True, True, False)
    assert "StorageType" in RAW_SCALAR_TYPES


def test_bindings_regeneration_is_byte_identical(tmp_path: pathlib.Path) -> None:
    out = tmp_path / "bindings.py"
    subprocess.run([sys.executable, "-m", "serpent._host._codegen", "--out", str(out)],
                   check=True, capture_output=True, text=True)
    committed = (ENV_JSON.parent / "bindings.py").read_bytes()
    assert out.read_bytes() == committed


def test_wasm_types_uniform_i64_at_this_pin() -> None:
    for fn in HOST_FUNCTIONS:
        assert all(t == "i64" for t in fn.wasm_params), fn.name
        assert fn.wasm_result == "i64", fn.name


def test_arg_names_present() -> None:
    put = functions_by_name["put_contract_data"]
    assert put.arg_names == ("k", "v", "t")
```

- [ ] **Step 2: RED** (`ModuleNotFoundError: serpent._host`).
- [ ] **Step 3: Implement** — copy env.json (verify both identities BEFORE
  committing); `_codegen.py` parses it and renders `bindings.py` as a literal
  `HOST_FUNCTIONS = (HostFn(...), ...)` tuple (no runtime json parsing — import-time
  cost stays trivial and the file is diffable); include min/max protocol from
  `min_supported_protocol`/`max_supported_protocol`.
- [ ] **Step 4: GREEN** + full gates.
- [ ] **Step 5: Commit** (`feat: add pinned host interface with generated bindings`).

---

### Task 2: Protocol-floor logic

**Files:**
- Modify: `src/serpent/_host/__init__.py`
- Test: `tests/unit/test_protocol_floor.py`

**Interfaces:**
- `DEFAULT_TARGET_PROTOCOL = 27`; `BASE_PROTOCOL = 20`
- `compute_protocol_floor(fn_names: Iterable[str]) -> int` — max of
  min_protocols (missing → BASE_PROTOCOL floor); unknown name → `KeyError`
  naming it.
- `check_protocol_target(fn_names, target) -> None` — raises
  `ProtocolGateError(ValueError)` naming EVERY offending function and its
  min_protocol when any fn requires more than `target`; also rejects fns whose
  `max_protocol < target` (the `t` module's gated dummy is the test vector).
- `declared_protocol(fn_names, requested: int | None) -> int` — THE value
  sub-plan D writes into `build_env_meta`. Per spec §4: default (requested is
  None — use an `is None` check, never truthiness) = `compute_protocol_floor(
  fn_names)`, NOT the target ceiling (a contract of ungated fns declares 20 and
  runs anywhere ≥ 20). `DEFAULT_TARGET_PROTOCOL = 27` is only the gate-check
  ceiling (`check_protocol_target(fn_names, requested if requested is not None
  else DEFAULT_TARGET_PROTOCOL)` runs first). An explicit `requested` below the
  computed floor is a `ValueError`. Tests: eight-ungated-fn set →
  `declared_protocol(...) == 20` and a `build_env_meta(20)` byte assertion
  alongside the 27 golden.

- [ ] **Step 1: Failing tests** — the eight Phase 0 fns → floor BASE_PROTOCOL;
  `extend_contract_data_ttl_v2` (min 26) in the set → floor 26;
  `delegate_account_auth` (min 27) → floor 27; a p28 fn (e.g.
  `sparse_map_new_from_linear_memory`) vs target 27 → ProtocolGateError naming it;
  `protocol_gated_dummy` (max 19) vs target 27 → ProtocolGateError;
  declared_protocol raise-only behavior both directions; unknown name KeyError.
- [ ] **Step 2-4: RED → implement → GREEN + gates.**
- [ ] **Step 5: Commit** (`feat: add computed protocol floor and gate checks`).

---

### Task 3: Type mapping — metadata annotations → SCSpecTypeDef

**Files:**
- Create: `src/serpent/spec/__init__.py`, `src/serpent/spec/typemap.py`
- Modify: `pyproject.toml` ([project.optional-dependencies] spec extra),
  `uv.lock` (regenerate via `uv lock` — CI's `uv run --frozen` fails otherwise).
  NOTE: `uv sync --all-groups` installs groups, not extras; serpent.spec is
  importable in CI only because the dev group already pins stellar-sdk — do NOT
  remove it from dev.
- Test: `tests/unit/test_typemap.py`, `tests/unit/test_core_zero_dep.py`

**Interfaces:**
- `typemap.to_spec_type(annotation: object) -> stellar_sdk.xdr.SCSpecTypeDef` —
  handles every mapping in Global Constraints, including parameterized
  `Vec[T]`/`Map[K, V]` (via typing.get_origin/get_args against the runtime
  classes), `X | None` → OPTION, `@contracttype` classes → UDT(name), and the
  fixed-length Bytes subclasses → BYTES_N(n). Unmappable annotation →
  `SpecTypeError(ValueError)` naming it (Env, Event, error enums, contract
  classes, plain int/str are all unmappable as spec types).
- `test_core_zero_dep.py`: walks ALL of `src/serpent/` EXCEPT `serpent/spec/`
  (explicitly including `serpent/__init__.py` and `serpent/_host/`), asserting
  every import resolves to stdlib (`sys.stdlib_module_names`) or serpent.
  `serpent.spec` is exempt, REQUIRES stellar_sdk, is NEVER re-exported from the
  package root, and never appears in `serpent.__all__` (test_public_api already
  pins the list — assert spec's absence explicitly here too).
- Staging note (supersedes File Structure comment): at THIS task,
  `spec/__init__.py` exports only `to_spec_type` and `SpecTypeError`; Task 4 adds
  the three builders (and its Files list includes Modify: spec/__init__.py).

- [ ] **Step 1: Failing tests** — one assertion per mapping row (construct the
  expected SCSpecTypeDef with stellar_sdk classes directly and compare
  `.to_xdr_bytes()`); parameterized nesting case `Vec[Map[Symbol, I128]]`;
  Option case `U32 | None`; UDT name from a @contracttype fixture; every
  unmappable → SpecTypeError. Zero-dep walker test.
- [ ] **Step 2-4: RED → implement → GREEN + gates.**
- [ ] **Step 5: Commit** (`feat: add chain-type to contract-spec type mapping`).

---

### Task 4: Section builders + goldens

**Files:**
- Create: `src/serpent/spec/sections.py`, `tests/goldens/env_meta_27.bin`,
  `tests/goldens/counter_spec.bin`
- Test: `tests/unit/test_sections.py`

**Interfaces:**
- `build_env_meta(protocol: int) -> bytes` (12-byte golden at 27).
- `build_spec_entries(contract_cls: type, *, types: Sequence[type] = ()) -> bytes`
  — one SC_SPEC_ENTRY_FUNCTION_V0 per contract method (from `_serpent_type_`
  metadata). **`__init__` IS emitted, renamed `__constructor`** (empty outputs;
  zero-arg constructors still emitted): the Stellar CLI derives deploy-time
  `--arg-name` flags from the spec's constructor entry, so omitting it makes
  parameterized contracts undeployable (adversarial-review-verified via
  `stellar contract deploy --help`). Validate the `__constructor` name through
  the same ≤30/Symbol checks (decorators skip `__init__` name checks — sections
  must not); test pins arg-name discoverability via SCSpecEntry.unpack.
  Docstrings → doc fields via `inspect.getdoc(method)` (full cleandoc'd text,
  matching Phase 0 behavior — NOT first-line-only), UTF-8 encoded; encoded
  length > 1024 raises `SpecDocError` naming the method and byte length
  (stellar_sdk enforces at pack; serpent pre-validates for a source-located
  error). Per-field/per-input docs are `b""` in M1-B (metadata carries no
  per-field doc — a real gap, noted for sub-plan C);
  UDT_STRUCT_V0 / UDT_ERROR_ENUM_V0 entries for each type in `types` (dispatch
  on `_serpent_type_["kind"]`). **EVENT_V0 emission is DEFERRED to sub-plan E**:
  SCSpecEventV0 requires `data_format` and per-param `location`, and the M1-A
  event metadata carries no topic/data split (decisions.md event ruling left
  topics call-site-level) — emitting a guessed entry would ship a valid-but-lying
  spec. `build_spec_entries` raises `SpecTypeError` naming any event class in
  `types`, message pointing at sub-plan E; test asserts the refusal. Name-cap
  validation with `SpecNameError` naming the offender. **Entry order pinned**:
  UDT structs (in `types` order), then error enums, then functions with
  `__constructor` first then declaration order (matches
  spikes/spike1/sections.py's recorded rationale); an order test independent of
  the golden bytes. Document that sub-plan D collects `types` from the module —
  a caller omitting `types` silently drops structs, so the docstring warns.
- `build_meta(name: str, version: str, pairs: Mapping[str, str] = {}) -> bytes` —
  SCMetaEntry stream; prepends ("name", name), ("version", version),
  ("serpentver", serpent.__version__) per spec §7; user pairs follow in given
  iteration order; a user key colliding with a reserved one is a ValueError
  naming it; pinned against serpent's own output (never byte-compared across
  toolchains).
- Golden acquisition (Step 1 sub-step): reconstruct the Phase 0 counter-interface
  spec bytes using `stellar_sdk` following `spikes/spike1/sections.py`'s recorded
  entry construction for `get`/`increment` (two functions, no inputs, u32 output,
  empty docs) and pin the result as `tests/goldens/counter_spec.bin` (64 bytes —
  the length is recorded in Phase 0 evidence; if the reconstruction is not exactly
  64 bytes, STOP and report BLOCKED rather than pinning a wrong golden), and
  assert `stellar_sdk`'s ContractSpec parser round-trips it. env_meta golden bytes
  from the constant above.

- [ ] **Step 1: Failing tests** — goldens byte-equal; a token_style-fixture
  build_spec_entries round-trips through `stellar_sdk` ScSpecEntry parsing with
  expected function names/arg types/doc strings; name-cap violations raise with
  the offender named; event entry shape (prefixTopics empty for now — document);
  error-enum entry carries the errorcode cases.
- [ ] **Step 2-4: RED → implement → GREEN + gates.**
- [ ] **Step 5: Commit** (`feat: add contract section builders with on-chain goldens`).

---

### Task 5: Drift gates + docs

**Files:**
- Create: `.github/workflows/` step additions if needed (env.json drift check is
  LOCAL-test-only for now — CI runs the suite which includes it; add a comment)
- Modify: `README.md` (short "architecture at a glance" section listing the
  layers built so far: val / types / decorators / env surface / _host / spec)
- Test: `tests/unit/test_host_bindings.py` (extend)

**Interfaces:**
- Extend the drift test: HostFn table invariants (export codes unique per module,
  sequential in declaration order over the base-63 alphabet `_0-9a-zA-Z`; arity ==
  len(arg_types) == len(arg_names); every arg/ret type resolves through the
  exhaustive `_model` type table AND the table's key set exactly equals the type
  set observed in env.json — an unrecognized type at re-pin time is a hard
  failure naming it).
- README section is prose, ≤ 40 lines, links the spec/decisions docs.

- [ ] **Step 1-4: tests → implement → green.**
- [ ] **Step 5: Commit** (`feat: harden host bindings invariants and document layers`).

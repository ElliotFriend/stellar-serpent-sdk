# Phase 0: Feasibility Spikes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire the two remaining feasibility risks before M1: (1) compile a contract written in the *designed* authoring style (class-based, typed, memory-requiring, error-raising) and verify it end-to-end on testnet including an invoke round-trip; (2) prove the real `soroban-env-host` can be embedded via PyO3 as the tier-2b test engine.

**Architecture:** Spike code lives in `spikes/` and is explicitly throwaway — its output is a findings report and a go/no-go, not kept code. The repo scaffold (Task 1) is the only permanent deliverable. Spike 1 is a vertical slice of the compiler pipeline from the spec (§4–§7) against a small fixed contract; Spike 2 is a minimal PyO3 wrapper over `soroban-sdk` testutils.

**Tech Stack:** Python ≥ 3.11, uv (uv_build backend), pytest, `stellar_sdk` ≥ 15 (XDR + RPC), wasmtime-py `==48.0.0`, `wasm-tools` CLI, `stellar` CLI ≥ 27.x, Rust + PyO3 0.23 + maturin (Spike 2 only).

**Spec:** `docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md`

## Global Constraints

- Python floor: 3.11. Package/import name: `serpent`.
- Validation gate: `wasm-tools validate --features=-all,mutable-global,sign-extension,bulk-memory` — every emitted module must pass, and `build.py` runs it BEFORE writing the output file (an invalid module is a compile error, never an artifact); the standalone CLI run is confirmation only.
- Target protocol 27 (current mainnet+testnet). Do not import any host fn with `min_supported_protocol > 27`. The vendored `env.json` is pinned to the `v28.0.2` tag (never `main`); record the pin in ACCEPTANCE.md.
- Error Vals: `(code << 32) | 3`. Never emit bare `unreachable` for a defined error.
- If any `*_linear_memory` host fn is imported, the module MUST declare one memory and export it as the literal name `memory` (assert at build time).
- All host imports are `(import "<1-char module>" "<code>" (func ... (param i64...) (result i64)))`. Pointer/length args to `*_linear_memory` fns are typed **U32Val** (tagged, `(x << 32) | 4`), NOT raw integers. Six raw-scalar interface types exist (StorageType: Temporary=0 Persistent=1 Instance=2 passed as bare u64).
- Symbols ≤ 9 chars from `[a-zA-Z0-9_]` pack inline (6 bits/char, high-order-first; `_`=1, `0-9`=2.., `A-Z`=12.., `a-z`=38..); longer symbols (≤ 32) require `symbol_new_from_linear_memory` (`b.j`).
- Commits: conventional commits, no emojis. Spike code commits use `spike:` prefix (nonstandard type is acceptable inside `spikes/`). Always stage explicit paths — never `git add -A`.
- Testnet deploys in this plan are pre-approved by the user (Phase 0 gate requires an on-chain invoke round-trip). Use a fresh throwaway identity, never a personal key.
- Tests must resolve repo paths via `pathlib.Path(__file__)`, never CWD-relative strings.

---

### Task 1: Permanent repo scaffold

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `README.md`, `src/serpent/__init__.py`, `tests/unit/test_scaffold.py`, `spikes/README.md`
- Note: `src/serpent/py.typed` is created by `uv init --lib` automatically; `.gitignore` is NOT (verified: uv skips it inside an existing git work tree) — we write it ourselves.

**Interfaces:**
- Produces: an installable empty `serpent` package; `uv run pytest` and `uv run ruff check` green; `spikes/` convention (throwaway, excluded from the package, still linted).

- [ ] **Step 1: Initialize the uv project (src layout, uv_build) and write .gitignore**

```bash
cd /Users/elliotvoris/Dev/stellar/sdk/py-soroban
uv init --lib --name serpent --python 3.11
```

Write `.gitignore`:

```gitignore
.venv/
__pycache__/
*.pyc
.mypy_cache/
.pytest_cache/
.ruff_cache/
.coverage
dist/
spikes/**/target/
spikes/**/*.wasm
test_snapshots/
```

Then edit `pyproject.toml` to exactly:

```toml
[project]
name = "serpent"
version = "0.0.1"
description = "Write Soroban smart contracts in Python (experimental)"
readme = "README.md"
requires-python = ">=3.11"
license = "Apache-2.0"
dependencies = []

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-cov>=7",
    "hypothesis>=6",
    "ruff>=0.16",
    "mypy>=2,<3",
    "stellar-sdk>=15,<16",
    "wasmtime==48.0.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
src = ["src", "tests", "spikes"]

[tool.mypy]
python_version = "3.11"
strict = true
files = ["src"]

[build-system]
requires = ["uv_build>=0.12,<0.13"]
build-backend = "uv_build"
```

- [ ] **Step 2: Write the smoke test and spikes convention**

`tests/unit/test_scaffold.py`:

```python
import serpent


def test_package_imports() -> None:
    assert serpent.__name__ == "serpent"
```

`spikes/README.md`:

```markdown
# Spikes — THROWAWAY CODE

Everything here is exploratory. It is not part of the serpent package, has no
quality bar beyond "answers the question," and must never be imported from src/.
Findings are recorded in docs/superpowers/specs/ reports; the code itself is
disposable evidence.
```

- [ ] **Step 3: Sync, lint, and run**

```bash
uv sync --all-groups && uv run ruff check src tests spikes && uv run pytest -q
```

Expected: ruff clean, `1 passed`.

- [ ] **Step 4: Verify external tools are present**

```bash
wasm-tools --version && stellar --version && uv run python -c "import stellar_sdk, wasmtime; print(stellar_sdk.__version__, wasmtime.__version__)"
```

Expected: all four versions print. If `wasm-tools` or `stellar` is missing, install via `brew install wasm-tools stellar-cli` and re-run.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore README.md .python-version uv.lock src tests spikes/README.md
git commit -m "chore: scaffold serpent uv project with test tooling

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Spike 1 — target contract, reference code, acceptance matrix

**Files:**
- Create: `spikes/spike1/contract_src.py`, `spikes/spike1/serpent_stub.py`, `spikes/spike1/ACCEPTANCE.md`, `spikes/spike1/reference/{pycomp.py,hostemu.py,mkmeta.py}` (copied)

**Interfaces:**
- Produces: the fixed input every later Spike-1 task compiles; the pass/fail matrix; committed reference copies of the original throwaway spike compiler (so Task 4 has a real starting point, not a hope).

- [ ] **Step 1: Copy the original spike reference code**

```bash
mkdir -p spikes/spike1/reference
cp /private/tmp/claude-501/-Users-elliotvoris-Dev-stellar-sdk-py-soroban/99f43ef6-7d92-4761-8061-a147c33946be/scratchpad/mine/{pycomp.py,hostemu.py,mkmeta.py} spikes/spike1/reference/
```

(If that scratchpad no longer exists, note it in ACCEPTANCE.md and size Task 4 Step 2 as a from-scratch ~200-line encoder.)

- [ ] **Step 2: Write the target contract in the DESIGNED authoring style**

`spikes/spike1/contract_src.py` — must be valid Python (`py_compile` is a hard gate). Create `spikes/spike1/serpent_stub.py` with typed no-op definitions (`Env`, `U32`, `Symbol`, `String`, `contract`, `contracttype`, `contracterror`) — use `typing.dataclass_transform` on `contracttype` so kwargs construction type-checks.

```python
from serpent_stub import Env, String, Symbol, U32, contract, contracterror, contracttype


@contracterror
class Error:
    LimitExceeded = 7


@contracttype
class Settings:
    counter_limit: U32      # 13 chars -> forces SymbolObject via linear memory
    display_name: String    # forces a string literal + data section


@contract
class Spike:
    def setup(env: Env, counter_limit: U32) -> None:
        """Store settings with a long-named field and a string literal."""
        settings = Settings(
            counter_limit=counter_limit,
            display_name=String("serpent phase zero"),
        )
        env.storage().instance().set(Symbol("SETTINGS"), settings)

    def bump(env: Env) -> U32:
        """Increment a persistent counter; raise LimitExceeded above the limit."""
        settings = env.storage().instance().get(Symbol("SETTINGS"), Settings)
        count = env.storage().persistent().get(Symbol("COUNT"), U32, default=U32(0))
        count = count + U32(1)
        if count > settings.counter_limit:
            raise Error.LimitExceeded
        env.storage().persistent().set(Symbol("COUNT"), count)
        return count
```

- [ ] **Step 3: Write the acceptance matrix**

`spikes/spike1/ACCEPTANCE.md`:

```markdown
env.json pin: rs-soroban-env tag v28.0.2

| # | Check | How verified |
|---|-------|--------------|
| 1 | Module passes `wasm-tools validate --features=-all,mutable-global,sign-extension,bulk-memory` | Task 4 |
| 2 | Module declares memory, exports it as `memory`, has a data section | Task 4 (wasm-tools print) |
| 3 | `stellar contract info interface --wasm` renders setup/bump + Settings struct + Error enum LOCALLY, pre-deploy; same via --id post-deploy | Task 4 + Task 6 |
| 4 | Deployed to testnet; `stellar contract fetch` bytes == local bytes | Task 6 |
| 5 | `setup(3)` then `bump()` x3 returns 1, 2, 3 on-chain | Task 6 |
| 6 | 4th `bump()` fails; the failure surfaces contract error code **7** (raw number in CLI output; not a generic trap) in simulation/getTransaction | Task 6 |
| 7 | Settings struct round-trips through instance storage on-chain — proven behaviorally: bump can only enforce counter_limit=3 (row 6) by reading back the Map that setup stored under a >9-char SymbolObject key. (Optional extra: getLedgerEntries on the ContractInstance entry via stellar_sdk.) | Task 6 |
| 8 | Same bytes produce 1,2,3 then code-7 failure in wasmtime harness locally | Task 5 |
| 9 | env-meta built with stellar_sdk XDR byte-matches the known-good 12-byte golden (protocol 27); spec section parses in the local interface render of row 3 | Task 4 |
| 10 | mypy --strict findings on the designed authoring surface recorded, each with a chosen resolution (feeds spec §2 amendment in Task 8) | Task 2 |
```

- [ ] **Step 4: Compile-check the source; RECORD (not gate) strict-mypy findings**

```bash
uv run python -m py_compile spikes/spike1/contract_src.py   # HARD GATE: must exit 0
uv run mypy --strict spikes/spike1/contract_src.py spikes/spike1/serpent_stub.py | tee spikes/spike1/mypy_findings.txt
```

The mypy run is EXPECTED to produce errors — that is the finding, not a failure.
Known-from-review classes to look for and record with a resolution decision each:
(a) `"self" parameter missing for a non-static method` on env-first methods — candidate resolutions: `@staticmethod`-implied-by-`@contract` mypy plugin (contradicts spec §2 "no plugins"), a documented `# serpent: no-self` convention, or amending the authoring model;
(b) `Settings(counter_limit=...)` kwargs — resolved by `dataclass_transform` in the stub (verify it clears);
(c) `raise Error.LimitExceeded` where the value is `int` — needs an authoring-surface decision (e.g. `@contracterror` members typed as exception instances). Fill acceptance row 10.

- [ ] **Step 5: Commit**

```bash
git add spikes/spike1 && git commit -m "spike: add phase-0 target contract, reference code, and acceptance matrix

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Spike 1 — frontend (designed-style AST → tiny IR)

**Files:**
- Create: `spikes/spike1/frontend.py`, `spikes/spike1/test_frontend.py`

**Interfaces:**
- Consumes: `contract_src.py` from Task 2.
- Produces: `parse_contract(path: str) -> ContractIR` where:

```python
@dataclass
class ContractIR:
    name: str
    errors: dict[str, dict[str, int]]           # {"Error": {"LimitExceeded": 7}}
    structs: dict[str, list[tuple[str, str]]]   # {"Settings": [("counter_limit", "U32"), ...]}
    functions: list[FuncIR]                     # name, params [(name, type)], ret type, body: list[Stmt]
```

`Stmt`/`Expr` node kinds needed (and ONLY these — reject everything else with `SpikeCompileError(msg, lineno, col)`): `StoreInstance`, `StoreDurable`, `LoadInstance`, `LoadDurable(default)`, `MakeStruct`, `GetField`, `LocalSet`, `LocalGet`, `AddU32`, `GtU32`, `IfRaise(code)`, `Return`, `ConstU32`, `ConstSymbol(str)`, `ConstString(str)`, `Param(i)`.

- [ ] **Step 1: Write failing tests for the resolver**

`spikes/spike1/test_frontend.py`:

```python
import pathlib
import tempfile

from frontend import SpikeCompileError, parse_contract

SPIKE_DIR = pathlib.Path(__file__).parent


def test_parses_target_contract() -> None:
    ir = parse_contract(str(SPIKE_DIR / "contract_src.py"))
    assert ir.name == "Spike"
    assert ir.errors["Error"]["LimitExceeded"] == 7
    assert ("counter_limit", "U32") in ir.structs["Settings"]
    assert [f.name for f in ir.functions] == ["setup", "bump"]
    setup = ir.functions[0]
    assert setup.params == [("counter_limit", "U32")] and setup.ret == "None"


def test_rejects_unsupported_with_location() -> None:
    src = (
        "from serpent_stub import Env, contract\n"   # line 1
        "@contract\n"                                 # line 2
        "class C:\n"                                  # line 3
        "    def f(env: Env) -> None:\n"              # line 4 (annotated: passes the annotation rule)
        "        for i in [1]:\n"                     # line 5 <- must be the reported failure
        "            pass\n"
    )
    p = pathlib.Path(tempfile.mkdtemp()) / "bad.py"
    p.write_text(src)
    try:
        parse_contract(str(p))
        raise AssertionError("should have raised")
    except SpikeCompileError as e:
        assert e.lineno == 5 and "For" in str(e)
```

Run: `uv run pytest spikes/spike1/test_frontend.py -v` — Expected: FAIL (module not found).

- [ ] **Step 2: Implement the frontend**

Walk `ast.parse(source)`: collect `@contracterror`/`@contracttype`/`@contract` ClassDefs by decorator name; require annotations on every contract-method param and return; resolve method bodies by *pattern-matching the known API shapes* (`env.storage().instance().set(...)` is an `ast.Call` whose func is an `Attribute` chain — match the chain textually: `("env","storage","instance","set")`). Every unmatched node raises `SpikeCompileError` with `node.lineno`/`node.col_offset`. No general name resolution — this is a spike; the finding is "the designed surface parses cleanly," not a production frontend.

- [ ] **Step 3: Run tests to green, lint, then commit**

```bash
uv run pytest spikes/spike1/test_frontend.py -v && uv run ruff check spikes
git add spikes/spike1 && git commit -m "spike: frontend parses designed authoring style to IR

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Spike 1 — emitter with memory, long symbols, error codes, sections

**Files:**
- Create: `spikes/spike1/emitter.py`, `spikes/spike1/sections.py`, `spikes/spike1/build.py`, `spikes/spike1/test_emitter.py`, `spikes/spike1/env.json` (vendored)

**Interfaces:**
- Consumes: `ContractIR` from Task 3; reference encoder `spikes/spike1/reference/pycomp.py`.
- Produces: `build.py` CLI: `uv run python spikes/spike1/build.py spikes/spike1/contract_src.py -o spikes/spike1/spike.wasm` producing a validated module (validation runs BEFORE the file is written). `sections.py` exposes `env_meta(protocol: int) -> bytes`, `spec_entries(ir: ContractIR) -> bytes`, `meta(pairs: dict[str, str]) -> bytes` — all built with `stellar_sdk.xdr` classes (this also proves the spec's §7 switch from hand-rolled XDR).

- [ ] **Step 1: Vendor the pinned env.json**

```bash
curl -fsSL https://raw.githubusercontent.com/stellar/rs-soroban-env/v28.0.2/soroban-env-common/env.json -o spikes/spike1/env.json
```

- [ ] **Step 2: Write failing golden tests for encodings we already know**

`spikes/spike1/test_emitter.py`:

```python
import pathlib

import pytest

from emitter import error_val, pack_u32val, symbol_small
from sections import env_meta

SPIKE_DIR = pathlib.Path(__file__).parent


def test_symbol_small_matches_rust_sdk_constant() -> None:
    # "COUNTER" packed by the Rust SDK == 253576579652878 (verified in research)
    assert symbol_small("COUNTER") == 253576579652878


def test_symbol_small_rejects_over_9() -> None:
    with pytest.raises(ValueError):
        symbol_small("counter_limit")  # 13 chars — must NOT silently overflow


def test_error_val_encoding() -> None:
    assert error_val(7) == (7 << 32) | 3


def test_u32val() -> None:
    assert pack_u32val(0) == 4 and pack_u32val(3_000_000_000) == (3_000_000_000 << 32) | 4


def test_env_meta_golden_bytes() -> None:
    # SCEnvMetaEntry(kind=0) + protocol=27 + preRelease=0, XDR-encoded: 12 bytes.
    assert env_meta(27) == bytes.fromhex("000000000000001b00000000")
```

Run: `uv run pytest spikes/spike1/test_emitter.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement `emitter.py`**

Start from `spikes/spike1/reference/pycomp.py`'s encoder core (LEB128 + section framing are reusable; the codegen is not). Required contents:

1. LEB128 (signed/unsigned), section framing, custom sections.
2. Sections emitted: Type(1), Import(2), Function(3), **Memory(5)**, **Export(7) including `("memory", mem 0)`**, Code(10), **Data(11)** — plus the three custom sections from `sections.py`.
3. Host imports used (NOTE: none of these eight carries a `min_supported_protocol` in env.json — the computed floor is the base protocol; env-meta is emitted at the DECLARED target, 27; `build.py` passes 27 to `env_meta()` and records both numbers): `l._ put_contract_data`, `l.1 get_contract_data`, `l.0 has_contract_data`, `m.9 map_new_from_linear_memory`, `m.1 map_get`, `b.j symbol_new_from_linear_memory`, `b.i string_new_from_linear_memory`, `x.5 fail_with_error`. All eight codes verified against env.json v28.0.2 during plan review — but the emitter must STILL look codes up by `name` from the vendored file at build time, never hardcode.
4. Data-section layout: pool the byte strings (`counter_limit`, `display_name`, `serpent phase zero`, plus map-key sort ordering — note `map_new_from_linear_memory` requires keys pre-sorted ascending as byte strings) at fixed offsets from 0; one 64 KiB page suffices.
5. `MakeStruct` lowers to `map_new_from_linear_memory(pack_u32val(keys_off), pack_u32val(vals_off), pack_u32val(n))` — the pointer/length args are **U32Vals, not raw integers** (env.json types them `U32Val`). Vals are written to a scratch offset at runtime with `i64.store`; keys are static. Same U32Val packing for `symbol_new_from_linear_memory(pack_u32val(off), pack_u32val(len))` and `string_new_from_linear_memory`.
6. `IfRaise(code)` lowers to: condition, `if`, `i64.const error_val(code)`, `call fail_with_error`, `drop`, `end` — code preserved, no `unreachable`.
7. ABI prologue on each export: for each `U32` param, check `(arg & 0xFF) == 4` else `fail_with_error(error_val(0xFFFF_FFFF))`; this is the minimal tag-check pattern from spec §4.
8. Every function body tracked with an operand-stack counter; `emit()` asserts balance at every `end` and emits real `return` opcodes (0x0F). Any imbalance raises before bytes are written.

- [ ] **Step 4: Implement `sections.py` with stellar_sdk XDR**

```python
from stellar_sdk import xdr


def env_meta(protocol: int) -> bytes:
    return xdr.SCEnvMetaEntry(
        kind=xdr.SCEnvMetaKind.SC_ENV_META_KIND_INTERFACE_VERSION,
        interface_version=xdr.SCEnvMetaEntryInterfaceVersion(
            protocol=xdr.Uint32(protocol), pre_release=xdr.Uint32(0)
        ),
    ).to_xdr_bytes()
```

(Constructor shapes verified against stellar-sdk 15.0.0 during plan review.) `spec_entries(ir)` builds one `SCSpecEntry` per function (`SC_SPEC_ENTRY_FUNCTION_V0`, docstring → `doc`), per struct (`UDT_STRUCT_V0`), per error enum (`UDT_ERROR_ENUM_V0`), concatenating `to_xdr_bytes()` streams. Note `doc`/`lib`/`name` fields are `bytes`, not `str`. Exact constructor signatures: read from the installed package (`uv run python -c "import inspect, stellar_sdk.xdr as x; print(inspect.signature(x.SCSpecFunctionV0.__init__))"`).

- [ ] **Step 5: `build.py` validates internally, then build + inspect locally**

`build.py` order of operations: compile → assemble bytes in memory → run `wasm-tools validate --features=-all,mutable-global,sign-extension,bulk-memory` on a temp file (subprocess) → only on success, write `-o` path; on failure, exit nonzero printing the validator output.

```bash
uv run python spikes/spike1/build.py spikes/spike1/contract_src.py -o spikes/spike1/spike.wasm
wasm-tools validate --features=-all,mutable-global,sign-extension,bulk-memory spikes/spike1/spike.wasm
wasm-tools print spikes/spike1/spike.wasm | head -50
stellar contract info interface --wasm spikes/spike1/spike.wasm
uv run pytest spikes/spike1 -v && uv run ruff check spikes
```

Expected: validate exits 0; the print shows `(memory ...)`, `(export "memory" ...)`, `(data ...)`; `info interface` renders setup/bump, the Settings struct with both long field names, and the Error enum with `LimitExceeded = 7` — **from the local file, before any deploy** (acceptance rows 3 + 9). Record the module size in ACCEPTANCE.md.

- [ ] **Step 6: Commit**

```bash
git add spikes/spike1 && git commit -m "spike: emit designed-style contract with memory, long symbols, and error codes

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Spike 1 — local wasmtime harness run

**Files:**
- Create: `spikes/spike1/harness.py`, `spikes/spike1/test_local_run.py`

**Interfaces:**
- Consumes: `spike.wasm` from Task 4.
- Produces: evidence for acceptance row 8; a mini-host implementing ONLY the eight imported host fns, with a `dict`-backed store and **u64 masking at every boundary** (`v & 0xFFFF_FFFF_FFFF_FFFF` on the way in, re-sign on the way out — the original spike emulator's live bug).

- [ ] **Step 1: Write the failing test**

```python
import pathlib

from harness import SpikeHost

WASM = str(pathlib.Path(__file__).parent / "spike.wasm")


def test_bump_sequence_and_error() -> None:
    host = SpikeHost(WASM)
    host.invoke("setup", [host.u32(3)])
    assert [host.invoke("bump", []) for _ in range(3)] == [host.u32(1), host.u32(2), host.u32(3)]
    err = host.invoke_expect_error("bump", [])
    assert err == (7 << 32) | 3        # contract error code 7 survives


def test_tag_check_prologue() -> None:
    host = SpikeHost(WASM)
    err = host.invoke_expect_error("setup", [2])   # Void where U32 expected
    assert err & 0xFF == 3             # rejected as an Error, not computed on
```

- [ ] **Step 2: Implement `harness.py`**

wasmtime `Config` mirrors the chain: multi_value/reference_types/simd/tail_call/threads all False (the full feature-set assertion test is deferred to M1 tier-2a — note that in FINDINGS). `fail_with_error` implemented as a Python exception carrying the masked error Val; `invoke_expect_error` catches it. Storage keyed by `(masked_key_val, storage_type)`; `map_new_from_linear_memory` reads keys/vals from exported memory via `instance.exports["memory"]`.

- [ ] **Step 3: Run to green, update ACCEPTANCE.md row 8, lint, commit**

```bash
uv run pytest spikes/spike1/test_local_run.py -v && uv run ruff check spikes
git add spikes/spike1 && git commit -m "spike: local wasmtime run verifies bump sequence and error code

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Spike 1 — testnet deploy + invoke round-trip (the gate evidence)

**Files:**
- Create: `spikes/spike1/DEPLOY_LOG.md`

**Interfaces:**
- Consumes: `spike.wasm`; acceptance matrix rows 3–7.

- [ ] **Step 1: Fresh throwaway identity + fund**

```bash
stellar keys generate serpent-spike --network testnet --fund
```

(No `--global` flag — it was removed from stellar-cli; verified against 27.1.0.)

- [ ] **Step 2: Deploy**

```bash
stellar contract deploy --wasm spikes/spike1/spike.wasm --source serpent-spike --network testnet
```

Record the contract ID in `DEPLOY_LOG.md`. Then verify byte-fidelity:

```bash
stellar contract fetch --id <CONTRACT_ID> --network testnet --out-file /tmp/fetched.wasm
cmp /tmp/fetched.wasm spikes/spike1/spike.wasm && echo IDENTICAL
```

- [ ] **Step 3: Confirm tooling interop on-chain (acceptance row 3, second half)**

```bash
stellar contract info interface --id <CONTRACT_ID> --network testnet
```

Expected: identical rendering to Task 4's local `--wasm` run. Paste output into DEPLOY_LOG.md.

- [ ] **Step 4: Invoke round-trip (rows 5–7)**

```bash
stellar contract invoke --id <CONTRACT_ID> --source serpent-spike --network testnet -- setup --counter_limit 3
for i in 1 2 3; do stellar contract invoke --id <CONTRACT_ID> --source serpent-spike --network testnet -- bump; done
stellar contract invoke --id <CONTRACT_ID> --source serpent-spike --network testnet -- bump   # 4th: must FAIL
```

Expected: returns 1, 2, 3, then a failure whose diagnostic/simulation output contains contract error **7** (the CLI prints the raw number, e.g. `Error(Contract, #7)` — it does not print your enum's variant name; that's expected, stellar-cli #2377). The 1,2,3-then-fail sequence is ALSO the row-7 proof: `bump` enforcing the limit demonstrates the Settings struct round-tripped through instance storage. If the error shows as a generic `InvalidAction` trap instead of code 7, the `fail_with_error` path is wrong — that is a spike FINDING (record it), not something to paper over.

- [ ] **Step 5: Record results and commit**

Fill every ACCEPTANCE.md row with PASS/FAIL + evidence. Commit:

```bash
git add spikes/spike1 && git commit -m "spike: record testnet deploy and invoke round-trip results

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Spike 2 — PyO3-embedded real host

**Files:**
- Create: `spikes/spike2/Cargo.toml`, `spikes/spike2/src/lib.rs`, `spikes/spike2/pyproject.toml`, `spikes/spike2/test_real_host.py`, `spikes/spike2/FINDINGS.md`

**Interfaces:**
- Consumes: `spikes/spike1/spike.wasm` (run the SAME bytes on the real host).
- Produces: a Python-importable `serpent_host` module exposing exactly:

```python
class RealEnv:
    def __init__(self) -> None: ...                      # Env with snapshots disabled + mock_all_auths()
    def register(self, wasm: bytes) -> str: ...          # -> contract address strkey (C...)
    def invoke(self, contract: str, func: str, args_xdr: list[bytes]) -> bytes: ...
        # args/result are ScVal XDR bytes; contract errors raise RuntimeError("contract error code N")
    def set_ledger(self, timestamp: int, sequence: int, protocol: int) -> None: ...
```

- [ ] **Step 1: Scaffold the crate**

`spikes/spike2/Cargo.toml`:

```toml
[package]
name = "serpent_host"
version = "0.0.1"
edition = "2021"

[lib]
name = "serpent_host"
crate-type = ["cdylib"]

[dependencies]
pyo3 = { version = "0.23", features = ["extension-module"] }
soroban-sdk = { version = "27", features = ["testutils"] }
```

`spikes/spike2/pyproject.toml`:

```toml
[project]
name = "serpent_host"
version = "0.0.1"
requires-python = ">=3.11"

[build-system]
requires = ["maturin>=1.0,<2.0"]
build-backend = "maturin"
```

Pin the exact `soroban-sdk` 27.x matching protocol 27 (27.0.6 is latest stable): run `cargo add soroban-sdk@27 --features testutils` in `spikes/spike2` and check `cargo tree | grep soroban-env-host` — record the resolved env-host version in FINDINGS.md. `Env::default()` on soroban-sdk 27 runs at protocol 27 (matches testnet — like-for-like with Task 6).

- [ ] **Step 2: Implement `lib.rs`**

Core shape (API names verified against docs.rs for soroban-sdk 27 during plan review):

```rust
use pyo3::prelude::*;
use pyo3::exceptions::PyRuntimeError;
use soroban_sdk::{Env, testutils::Ledger as _};
use soroban_sdk::xdr::{ScVal, Limits, ReadXdr, WriteXdr};

#[pyclass]
struct RealEnv { env: Env }

#[pymethods]
impl RealEnv {
    #[new]
    fn new() -> Self {
        // Env::new_with_config(EnvTestConfig { capture_snapshot_at_drop: false, .. })
        // — otherwise soroban-sdk's Drop impl writes test_snapshots/*.json into CWD.
        let env = Env::new_with_config(soroban_sdk::testutils::EnvTestConfig {
            capture_snapshot_at_drop: false,
            ..Default::default()
        });
        env.mock_all_auths();
        RealEnv { env }
    }

    fn register(&self, wasm: &[u8]) -> PyResult<String> {
        // Env::register<C: Register, A: ConstructorArgs> — impl Register for &[u8]
        let addr = self.env.register(wasm, ());
        Ok(addr.to_string().to_string())
    }

    fn invoke(&self, contract: &str, func: &str, args_xdr: Vec<Vec<u8>>) -> PyResult<Vec<u8>> {
        // Conversion path (verify names against pinned soroban-sdk 27.x docs.rs):
        //   ScVal::from_xdr(bytes, Limits::none()) -> scval.try_into_val(&self.env) -> Val
        //   NOTE: Address::from_string takes a soroban String, and Address has no Display —
        //   build soroban_sdk::String::from_str(&env, contract) first.
        //   Symbol::try_from_val(&env, func)
        //   env.try_invoke_contract::<Val, soroban_sdk::Error>(&addr, &sym, args_vec)
        //     returns Result<Result<T, T::Error>, Result<E, InvokeError>>;
        //     contract errors arrive in the Err(Ok(e)) arm, e.get_code() -> u32.
        //   Ok(val)   -> ScVal::try_from_val(&env, &val) -> to_xdr(Limits::none())
        //   Err(Ok(e)) -> PyRuntimeError(format!("contract error code {}", e.get_code()))
        //   Err(Err(invoke_err)) -> PyRuntimeError(format!("{invoke_err:?}"))
        // Budget half a day; every API mismatch discovered goes in FINDINGS.md.
        todo!("~40 lines following the path above")
    }

    fn set_ledger(&self, timestamp: u64, sequence: u32, protocol: u32) -> PyResult<()> {
        self.env.ledger().with_mut(|l| { l.timestamp = timestamp; l.sequence_number = sequence; l.protocol_version = protocol; });
        Ok(())
    }
}

#[pymodule]
fn serpent_host(m: &Bound<'_, PyModule>) -> PyResult<()> { m.add_class::<RealEnv>() }
```

- [ ] **Step 3: Build and run against spike.wasm**

```bash
cd spikes/spike2 && uvx maturin develop && cd ../..
uv run pytest spikes/spike2/test_real_host.py -v
```

(`uvx maturin develop` works here — maturin auto-detects the repo-root `.venv`. CAVEAT: any later `uv sync` PRUNES the maturin-installed module; re-run `uvx maturin develop` after every `uv sync`.)

`test_real_host.py`:

```python
import pathlib

import pytest
import serpent_host
from stellar_sdk import scval, xdr

WASM = (pathlib.Path(__file__).parent.parent / "spike1" / "spike.wasm").read_bytes()


def _u32(result_xdr: bytes) -> int:
    return scval.from_uint32(xdr.SCVal.from_xdr_bytes(result_xdr))


def test_same_bytes_on_real_host() -> None:
    env = serpent_host.RealEnv()
    cid = env.register(WASM)
    env.invoke(cid, "setup", [scval.to_uint32(3).to_xdr_bytes()])
    results = [_u32(env.invoke(cid, "bump", [])) for _ in range(3)]
    assert results == [1, 2, 3]
    with pytest.raises(RuntimeError, match=r"contract error code 7\b"):
        env.invoke(cid, "bump", [])
```

Expected: PASS — the same bytes behave identically on the real host as in Task 5's mini-host and Task 6's testnet run. Any divergence is a headline finding.

- [ ] **Step 4: Measure and record**

Time 1,000 `bump` invocations (`time.perf_counter` loop) and one `RealEnv()+register`. Record in FINDINGS.md: perf numbers, wheel-build friction, total Rust LOC, API pain points, resolved soroban-env-host version, and a recommendation: **adopt as tier-2b / fall back to quickstart-RPC**.

- [ ] **Step 5: Commit**

```bash
git add spikes/spike2 && git commit -m "spike: embed soroban-env-host via PyO3 and run spike contract on real host

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Phase 0 findings report and gate decision

**Files:**
- Create: `docs/superpowers/specs/2026-XX-XX-phase0-findings.md` (date it when written)
- Modify: `docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md` (only if findings invalidate a spec claim)

- [ ] **Step 1: Write the findings report**

Sections: acceptance matrix results (all 10 rows, with contract IDs and CLI output excerpts); the **spec §2 amendment proposal** from the strict-mypy findings (row 10: no-self methods, error-raise typing — each with the chosen resolution); Spike 2 recommendation with numbers; every surprise discovered (API mismatches, encoding corrections, effort actuals); explicit GO / NO-GO / GO-WITH-CHANGES for M1, listing any spec sections that must change.

- [ ] **Step 2: Update the spec if needed, commit both**

```bash
git add docs/superpowers && git commit -m "docs: record phase 0 spike findings and gate decision

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

- [ ] **Step 3: STOP — user checkpoint**

Present the findings and the gate decision to the user. The M1 implementation plan is written only after this conversation, informed by the findings. Do not begin M1 work under this plan.

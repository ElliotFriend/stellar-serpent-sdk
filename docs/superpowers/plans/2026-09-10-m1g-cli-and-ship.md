# M1-G: CLI plugin + ship — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship serpent's M1 in the shape a user meets it: the `stellar-serpent` Stellar CLI plugin (`build`/`inspect`/`doctor`), a seventh example that touches every M1 surface, the docs site, CI's Rust and docs jobs, the carried hygiene pass, and the one user-approved testnet deployment that closes M1 and retires the B1 divergence.

**Architecture:** One stdlib `argparse` module (`serpent/cli.py`) fronts the existing `build_file`/`compile_module` API and two new read-side helpers (`serpent/spec/decode.py` for the custom sections' XDR, `serpent/emitter/artifact.py` for the section/import/protocol facts of a built module). Everything user-visible is generated FROM the code it describes (examples rendered from `examples/*.py`, the CLI reference from `--help` goldens, the subset page from `must_reject/`), and CI gains a Rust job that runs the real-host suite with `SERPENT_REQUIRE_REAL_HOST=1`. The last task is a checklist Elliot executes at the hard stop.

**Tech Stack:** Python ≥ 3.11 stdlib (`argparse`, `hashlib`, `json`, `shutil`, `subprocess`, `urllib`); `stellar-sdk>=15,<16` (the `spec` extra) for XDR decoding; mkdocs-material 9.7 + mkdocstrings-python 2.0 (a `docs` dependency group); GitHub Actions with `dtolnay/rust-toolchain` + `Swatinem/rust-cache` + maturin; the stock `stellar` CLI (27.1.0 locally, 28.0.0 in the CI smoke) for deployment.

**Spec:** `docs/superpowers/specs/2026-09-10-m1g-inputs-dossier.md` (the G dossier; cite its IDs — S#, R#, D#, P#, U#, O#, K#, C#) over `docs/superpowers/specs/2026-08-26-serpent-python-soroban-sdk-design.md` §9 (the CLI), §3 (layout), §11 (M1 scope + the deployment). Rulings: `docs/superpowers/decisions.md` 2026-09-10 "M1-G rulings (dossier E1-E16)". Process: `docs/superpowers/process.md`.

## Global Constraints

- **Zero-dep core** (D1, C14): `serpent/cli.py` imports stdlib + `serpent` at module load; `serpent.emitter`/`serpent.spec` only INSIDE `build`/`inspect`; `stellar_sdk` is never imported by `cli.py` directly — XDR decoding lives in `serpent/spec/decode.py` (the exempt subpackage). `serpent/__init__.py` never imports `cli` (a not-reachable-from-root test, like `spec`/`testing`). **Plan-author correction to ruling E2's letter**: `cli.py` stays a CORE module under the zero-dep walk (it needs no exemption because it never spells `stellar_sdk`); the not-reachable-from-root half of E2 stands. Ratify or overturn at plan review.
- **`serpent.__all__` is frozen at 40 names** (D8, C13): the CLI adds nothing to it; `test_public_api.py` is not edited except in Task 11 (`__version__`).
- **Registry discipline** (D2): `codes.py` is edited ONLY in Task 7, ONLY for the enumerated sanctioned wording/origin edits; no new codes, no renumbering, no meaning reversal; `docs/subset.md` and every snapshot pin regenerate in the same commit. Any other apparent need for a registry edit → return BLOCKED.
- **`decisions.md`, `spikes/` (except `spikes/README.md` in Task 10), and `sandbox/counter.py`/`sandbox/hello_world.py` are never edited by an implementer** (process.md; C7 item 4).
- **The four gates on every task**, non-negotiable: `uv run --no-sync ruff check .`; `uv run --no-sync ruff format --check src tests examples`; `uv run --no-sync mypy --strict`; `SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q`. The host extension is built in this checkout (K14); use `--no-sync` always (D10's prune trap). Baseline at main 7671bcb: 4614 passed / 7 skipped.
- **No pushes, no publishes, no deployments by an implementer** (D16). Task 11's chain writes are Elliot's, after an explicit in-session approval.
- **Commit style**: conventional commits, imperative, no emoji, no em dashes, Oxford commas; AI attribution trailer on model-authored commits; try signed with a 40 s timeout, fall back to `--no-gpg-sign` + append `<sha> <subject>` to `.git/unsigned-commits.log` (process.md).
- **Version pins the plan relies on** (K7/K8): `mkdocs-material>=9.7,<10`, `mkdocstrings-python>=2,<3`, wasm-tools `1.258.0` (ci.yml), stellar-cli `28.0.0` (CI smoke tarball), Python 3.11 for the Rust job.
- **Exit codes** (ruling E4/D.1): 0 ok; 1 the contract was rejected (rendered diagnostics) or the artifact is malformed; 2 usage (argparse's own); 3 environment (missing extra, missing/unwritable file, reserved meta key, wasm-tools required but absent, a `fail` doctor row).
- **Determinism**: every `--help` golden is rendered at a fixed width (80 columns) via the parser's `formatter_class`, never the terminal's.

---

## File Structure

**Created**

| File | Responsibility |
|---|---|
| `src/serpent/_pins.py` | The ONE home of toolchain pins the CLI and tests read: `WASM_TOOLS_PIN = "1.258.0"`. Stdlib-only. |
| `src/serpent/cli.py` | `stellar-serpent`: parser construction, the three subcommands, exit-code mapping, `main(argv) -> int`. Lazy heavy imports. |
| `src/serpent/spec/decode.py` | Read-direction XDR: `decode_env_meta`, `decode_spec_entries`, `decode_meta`, `spec_entry_names_by_kind`. Imports `stellar_sdk` (exempt subpackage). |
| `src/serpent/emitter/artifact.py` | `inspect_artifact(wasm) -> Artifact`: sections, imports→HostFn, exports, declared vs recomputed protocol, spec/meta summaries. |
| `examples/bounty_board.py` | The seventh example (moved from `sandbox/`, U1). |
| `tests/unit/test_cli.py` | In-process `main([...])` tests for every subcommand and exit code; the `--help` goldens harness. |
| `tests/unit/test_artifact.py` | `inspect_artifact` over the seven examples + the deployed shapes bytes + a hand-assembled gated module. |
| `tests/unit/test_spec_decode.py` | Round-trips: `build_*` → `decode_*`. |
| `tests/unit/test_pins.py` | `WASM_TOOLS_PIN == ci.yml's WASM_TOOLS_VERSION`. |
| `tests/goldens/cli/*.help.txt` | `--help` goldens (root, build, inspect, doctor). |
| `tests/goldens/wasm/bounty_board.wat.txt` | The seventh example's disassembly snapshot. |
| `tests/real_host/test_example_bounty_board_real.py` | The bounty board's real-host leg (Elliot's five tests, typed). |
| `tests/must_reject/names/param_shadows_declared_type.py` | The O-HYG5 fixture. |
| `tests/unit/test_docs_site.py` | mkdocs config + example-page drift tests. |
| `mkdocs.yml`, `docs/index.md`, `docs/getting-started.md`, `docs/cli.md`, `docs/api.md`, `docs/examples/*.md`, `docs/deployments.md` | The site (Task 8; deployments filled in Task 11). |

**Modified**

| File | Change |
|---|---|
| `pyproject.toml` | `[project.scripts]`; `docs` dependency group; version 0.1.0 (Task 11). |
| `tests/unit/test_core_zero_dep.py` | The `cli` not-reachable-from-root test + probe line. |
| `tests/unit/test_emitter_end_to_end.py`, `test_emitter_printer.py`, `test_harness_hostfns.py`, `test_frontend_fuzz.py`, `test_examples.py`, `tests/real_host/test_examples_real.py` | The seventh example joins every inventory; the cross-inventory test. |
| `src/serpent/compiler/frontend.py:441-443` | SPT2004 for a parameter shadowing a module-level reservation. |
| `tests/unit/test_bridging_completeness.py:508-577` | Derived raise-site walk replaces the hand-kept tuple. |
| `tests/harness/hostfns.py` (FullHost) | `strict_obj_cmp=True` default. |
| `tests/unit/test_emitter_symbol_compare.py` | `StrictObjCmpHost` retired in favour of the default. |
| `src/serpent/compiler/codes.py`, `loader.py`, `diagnostics.py`, `spec/sections.py`, `examples/shapes.py`, `tests/must_reject/**` headers, `docs/subset.md`, `tests/goldens/wasm/shapes.wat.txt` | The sanctioned wording pass (Task 7). |
| `.github/workflows/ci.yml` | `real-host`, `docs`, `cli-install` jobs. |
| `README.md`, `sandbox/README.md`, `sandbox/compile.py`, `spikes/README.md`, `docs/testing.md`, `tests/unit/test_no_stale_promises.py` | Task 10. |
| `tests/real_host/test_testnet_fixtures.py`, `tests/real_host/fixtures/testnet/**` | Task 11 (table-driven fixture sets; the flip; re-recorded fixtures). |

**Model seating** (process.md): Tasks 1, 2, 4, 7, 8, 9, 10 → Sonnet implementer + Sonnet review; Tasks 3, 5, 6 → Opus implementer + Opus review (protocol recomputation, frontend semantics, the tier-2a oracle); Task 11 → controller + Elliot (code halves: Sonnet).

**Task order**: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11. Tasks 5, 6, 7 are independent of each other and of 4; Task 9 (CI) needs 8 (the docs job builds `mkdocs.yml`) and 1 (the pin test); Task 10 needs everything before it (the README describes the shipped shape); Task 11 is last and gated.

---

### Task 1: `serpent.cli` skeleton, `doctor`, the console script, and the pin home

**Files:**
- Create: `src/serpent/_pins.py`, `src/serpent/cli.py`, `tests/unit/test_cli.py`, `tests/unit/test_pins.py`, `tests/goldens/cli/root.help.txt`, `tests/goldens/cli/doctor.help.txt`
- Modify: `pyproject.toml` (`[project.scripts]`), `tests/unit/test_core_zero_dep.py:91-147`
- Test: `tests/unit/test_cli.py`, `tests/unit/test_pins.py`, `tests/unit/test_core_zero_dep.py`

**Interfaces:**
- Produces: `serpent.cli.main(argv: Sequence[str] | None = None) -> int`; `serpent.cli.build_parser() -> argparse.ArgumentParser` (subcommands `build`, `inspect`, `doctor`; `build`/`inspect` are REGISTERED here with `set_defaults(run=...)` pointing at stubs that return `EXIT_ENVIRONMENT` with "not yet implemented" — Tasks 2/3 replace the bodies, not the registration, so the root `--help` golden is final from this task); `serpent.cli.Check`, `serpent.cli.Probes`, `serpent.cli.run_doctor(probes: Probes, network: str | None) -> list[Check]`; the exit-code constants `EXIT_OK = 0`, `EXIT_REJECTED = 1`, `EXIT_USAGE = 2`, `EXIT_ENVIRONMENT = 3`; `SPEC_EXTRA_HINT: str`; `HELP_WIDTH = 80`.
- Produces: `serpent._pins.WASM_TOOLS_PIN = "1.258.0"`.
- Produces (test harness): `tests/unit/test_cli.py`'s `golden(name: str, text: str) -> None` (write-then-compare under `SERPENT_REGEN_GOLDENS=1`, `tests/unit/test_emitter_printer.py:351-433`'s shape) — Tasks 2 and 3 reuse it for their subcommand goldens.

- [ ] **Step 1: The pin home and its equality test (write the failing test first)**

Create `tests/unit/test_pins.py`:

```python
"""`serpent._pins`: one home for the toolchain versions CI and `doctor` agree on (ruling E13)."""

from __future__ import annotations

import re
from pathlib import Path

from serpent import _pins

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CI = _REPO_ROOT / ".github" / "workflows" / "ci.yml"


def test_wasm_tools_pin_matches_the_ci_workflow() -> None:
    """The workflow's `WASM_TOOLS_VERSION` and `_pins.WASM_TOOLS_PIN` are two
    spellings of one decision; this is what keeps them one."""
    text = _CI.read_text(encoding="utf-8")
    found = re.findall(r'WASM_TOOLS_VERSION:\s*"([0-9][0-9.]*)"', text)
    assert found, "ci.yml no longer pins WASM_TOOLS_VERSION"
    assert set(found) == {_pins.WASM_TOOLS_PIN}, (found, _pins.WASM_TOOLS_PIN)


def test_the_pin_is_a_release_version_string() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", _pins.WASM_TOOLS_PIN)
```

Run: `uv run --no-sync pytest -q tests/unit/test_pins.py` → FAIL (`ModuleNotFoundError: serpent._pins`).

Create `src/serpent/_pins.py`:

```python
"""Toolchain pins the CLI and the tests read from ONE place (ruling E13, O-CI2).

`.github/workflows/ci.yml` installs `wasm-tools` at exactly `WASM_TOOLS_PIN`;
`tests/unit/test_pins.py` asserts the two spellings agree, and `stellar-serpent
doctor` compares a contributor's local `wasm-tools --version` against it.
Bumping the pin is a deliberate two-line edit (here and in ci.yml) documented
in `docs/testing.md`; nothing polls for a newer release.

Stdlib-only, imported by `serpent.cli` at module load, so it must never grow a
dependency.
"""

#: The exact `wasm-tools` release CI installs and `doctor` recommends.
WASM_TOOLS_PIN = "1.258.0"
```

Run: `uv run --no-sync pytest -q tests/unit/test_pins.py` → 2 passed.

- [ ] **Step 2: The not-reachable-from-root test for `cli` (failing first)**

In `tests/unit/test_core_zero_dep.py`, after `test_serpent_testing_is_not_reachable_from_the_package_root`, add:

```python
def test_serpent_cli_is_not_reachable_from_the_package_root() -> None:
    """`serpent.cli` is a TOOL, not authoring surface (ruling G-E2): `import
    serpent` must never load it (it would drag `argparse` and, through
    `build`/`inspect`, the `spec` extra into every contract's import). It is
    NOT in `EXEMPT`: `cli.py` spells no foreign import at any level -- the XDR
    it needs lives in `serpent.spec.decode` -- so the zero-dep walk covers it
    like any other core module (plan-author correction to E2's letter)."""
    assert "cli" not in serpent.__all__
    source = (SRC / "__init__.py").read_text(encoding="utf-8")
    assert "from serpent.cli" not in source
    assert "import serpent.cli" not in source
    assert "from .cli" not in source
    assert "from . import cli" not in source
    assert "from serpent import cli" not in source
    assert (SRC / "cli.py").is_file()
```

And in `test_importing_serpent_does_not_load_stellar_sdk`, extend the probe string with one more assertion line before `print('ok')`:

```python
        "assert 'serpent.cli' not in sys.modules, 'serpent core pulled in serpent.cli';"
```

Run: `uv run --no-sync pytest -q tests/unit/test_core_zero_dep.py -k cli` → FAIL (`cli.py` does not exist).

- [ ] **Step 3: The doctor tests (failing first)**

Create `tests/unit/test_cli.py`:

```python
"""`stellar-serpent` (spec §9; dossier D.1): every subcommand driven IN-PROCESS.

`serpent.cli.main` takes `argv` and returns the exit code, so nothing here
spawns a subprocess -- except the one golden test that proves the console
script is installed under the name the Stellar CLI's plugin discovery looks
for (K4: `stellar serpent ...` dispatches to a `stellar-serpent` on PATH).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from serpent import _pins, cli

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "goldens" / "cli"
REGEN_ENV = "SERPENT_REGEN_GOLDENS"
REGEN_HINT = f"{REGEN_ENV}=1 uv run --no-sync pytest tests/unit/test_cli.py"


def golden(name: str, text: str) -> None:
    """Write-then-compare, exactly `tests/unit/test_emitter_printer.py`'s shape:
    with `SERPENT_REGEN_GOLDENS=1` the file is (re)written and the test passes;
    otherwise a missing or differing golden fails with the regen hint."""
    path = GOLDEN_DIR / f"{name}.help.txt"
    if os.environ.get(REGEN_ENV) == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    if not path.exists():
        pytest.fail(f"no golden for {name!r} at {path}; create it with `{REGEN_HINT}`")
    assert text == path.read_text(encoding="utf-8"), f"golden drifted; regen with `{REGEN_HINT}`"


# --- the parser and the goldens -----------------------------------------------


def test_root_help_golden() -> None:
    golden("root", cli.build_parser().format_help())


def test_doctor_help_golden() -> None:
    golden("doctor", cli.subparser("doctor").format_help())


def test_help_is_rendered_at_a_fixed_width(monkeypatch: pytest.MonkeyPatch) -> None:
    """The goldens must not depend on the terminal: `COLUMNS` is ignored."""
    monkeypatch.setenv("COLUMNS", "200")
    wide = cli.build_parser().format_help()
    monkeypatch.setenv("COLUMNS", "40")
    narrow = cli.build_parser().format_help()
    assert wide == narrow


def test_no_subcommand_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main([])
    assert info.value.code == cli.EXIT_USAGE


# --- doctor --------------------------------------------------------------------


def _probes(
    *,
    which: dict[str, str | None] | None = None,
    modules: dict[str, ModuleType | Exception] | None = None,
    runs: dict[str, str] | None = None,
    python: tuple[int, int, int] = (3, 11, 7),
) -> cli.Probes:
    """A `Probes` whose every answer is scripted, so each doctor row can be
    driven into every state without touching this machine's toolchain."""
    which_table = which or {}
    module_table = modules or {}
    run_table = runs or {}

    def _which(name: str) -> str | None:
        return which_table.get(name)

    def _import(name: str) -> ModuleType:
        answer = module_table.get(name, ModuleNotFoundError(name))
        if isinstance(answer, Exception):
            raise answer
        return answer

    def _run(argv: list[str]) -> str:
        return run_table.get(argv[0], "")

    return cli.Probes(which=_which, import_module=_import, run=_run, python_version=python)


def _module(**attrs: Any) -> ModuleType:
    module = ModuleType("scripted")
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def _row(checks: list[cli.Check], name: str) -> cli.Check:
    (row,) = [c for c in checks if c.name == name]
    return row


def test_doctor_rows_are_the_documented_set_in_order() -> None:
    checks = cli.run_doctor(_probes(), network=None)
    assert [c.name for c in checks] == [
        "python",
        "serpent",
        "spec extra",
        "wasm-tools",
        "stellar CLI",
        "plugin on PATH",
        "serpent_host",
        "env.json pin",
        "default target protocol",
    ]


def test_python_below_3_11_fails() -> None:
    checks = cli.run_doctor(_probes(python=(3, 10, 12)), network=None)
    assert _row(checks, "python").status == "fail"
    assert cli.exit_code_for(checks) == cli.EXIT_ENVIRONMENT


def test_missing_spec_extra_fails_with_the_install_hint() -> None:
    checks = cli.run_doctor(_probes(), network=None)
    row = _row(checks, "spec extra")
    assert row.status == "fail"
    assert row.remedy == cli.SPEC_EXTRA_HINT


def test_spec_extra_present_in_range_is_ok() -> None:
    probes = _probes(modules={"stellar_sdk": _module(__version__="15.2.0")})
    assert _row(cli.run_doctor(probes, None), "spec extra").status == "ok"


def test_spec_extra_out_of_range_warns() -> None:
    probes = _probes(modules={"stellar_sdk": _module(__version__="16.0.0")})
    assert _row(cli.run_doctor(probes, None), "spec extra").status == "warn"


def test_wasm_tools_absent_warns_and_names_the_pin() -> None:
    row = _row(cli.run_doctor(_probes(), None), "wasm-tools")
    assert row.status == "warn"
    assert _pins.WASM_TOOLS_PIN in row.remedy


def test_wasm_tools_at_the_pin_is_ok() -> None:
    probes = _probes(
        which={"wasm-tools": "/usr/local/bin/wasm-tools"},
        runs={"/usr/local/bin/wasm-tools": f"wasm-tools {_pins.WASM_TOOLS_PIN}\n"},
    )
    assert _row(cli.run_doctor(probes, None), "wasm-tools").status == "ok"


def test_wasm_tools_off_the_pin_warns() -> None:
    probes = _probes(
        which={"wasm-tools": "/usr/local/bin/wasm-tools"},
        runs={"/usr/local/bin/wasm-tools": "wasm-tools 1.200.0\n"},
    )
    row = _row(cli.run_doctor(probes, None), "wasm-tools")
    assert row.status == "warn"
    assert "1.200.0" in row.detail and _pins.WASM_TOOLS_PIN in row.detail


def test_plugin_not_on_path_warns() -> None:
    row = _row(cli.run_doctor(_probes(), None), "plugin on PATH")
    assert row.status == "warn"
    assert "stellar serpent" in row.remedy


def test_plugin_on_path_is_ok() -> None:
    probes = _probes(which={"stellar-serpent": "/somewhere/bin/stellar-serpent"})
    assert _row(cli.run_doctor(probes, None), "plugin on PATH").status == "ok"


def test_serpent_host_absent_is_info_with_the_rebuild_command() -> None:
    row = _row(cli.run_doctor(_probes(), None), "serpent_host")
    assert row.status == "info"
    assert "maturin develop" in row.remedy


def test_doctor_exit_code_is_zero_without_a_fail_row() -> None:
    probes = _probes(modules={"stellar_sdk": _module(__version__="15.2.0")})
    checks = cli.run_doctor(probes, None)
    assert all(c.status != "fail" for c in checks)
    assert cli.exit_code_for(checks) == cli.EXIT_OK


def test_doctor_json_is_a_list_of_rows(capsys: pytest.CaptureFixture[str]) -> None:
    """`--json` on the REAL probes: the row set is fixed, so the shape can be
    asserted without scripting this machine's answers."""
    assert cli.main(["doctor", "--json"]) in (cli.EXIT_OK, cli.EXIT_ENVIRONMENT)
    rows = json.loads(capsys.readouterr().out)
    assert [r["name"] for r in rows][:2] == ["python", "serpent"]
    assert {r["status"] for r in rows} <= {"ok", "warn", "fail", "info"}


def test_doctor_human_output_has_one_line_per_row(capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["doctor"])
    out = capsys.readouterr().out
    for name in ("python", "serpent", "spec extra", "wasm-tools"):
        assert name in out


def test_network_probe_reports_the_protocol() -> None:
    def fetch(_url: str) -> dict[str, Any]:
        return {"protocolVersion": 28}

    checks = cli.run_doctor(_probes(), network="testnet", fetch_version_info=fetch)
    row = _row(checks, "network")
    assert row.status == "info" and "28" in row.detail


# --- the console script -----------------------------------------------------------


def test_the_console_script_is_installed_under_the_plugin_name() -> None:
    """K4: the Stellar CLI finds a plugin as `stellar-<name>` on PATH. The repo
    venv installs `serpent` editable, so the script exists beside this
    interpreter and answers `--help` with exit 0."""
    script = Path(sys.executable).with_name("stellar-serpent")
    assert script.exists(), f"{script} missing: is [project.scripts] declared and the venv synced?"
    done = subprocess.run([str(script), "--help"], capture_output=True, text=True, check=False)
    assert done.returncode == cli.EXIT_OK, done.stderr
    assert "build" in done.stdout and "inspect" in done.stdout and "doctor" in done.stdout
```

Run: `uv run --no-sync pytest -q tests/unit/test_cli.py` → FAIL at import (`serpent.cli` missing).

- [ ] **Step 4: `serpent/cli.py` — parser, `doctor`, stubs for `build`/`inspect`**

Create `src/serpent/cli.py`:

```python
"""`stellar-serpent`: serpent as a Stellar CLI plugin (spec §9; G dossier D.1).

The Stellar CLI discovers a plugin as an executable named `stellar-<name>` on
PATH and forwards the remaining arguments verbatim (K4: `stellar serpent build
x.py` runs `stellar-serpent build x.py`). `pyproject.toml`'s `[project.scripts]`
installs this module's `main` under that name, so `uv tool install` is the
whole installation.

Three subcommands, one exit-code vocabulary (ruling E4):

* `build`   -- compile + emit a contract to `.wasm` (`serpent.emitter.build_file`);
* `inspect` -- the facts about a built artifact the stock CLI cannot know
  (`serpent.emitter.artifact.inspect_artifact`; ruling E3);
* `doctor`  -- offline toolchain checks with a remedy per row.

Exit codes: `EXIT_OK` 0; `EXIT_REJECTED` 1 (the contract does not compile --
the rendered diagnostics ARE the output -- or the artifact is malformed);
`EXIT_USAGE` 2 (argparse's own); `EXIT_ENVIRONMENT` 3 (a missing extra or
file, an unwritable output, a reserved meta key, `wasm-tools` required but
absent, or a `fail` row from `doctor`).

**Import discipline (ruling E2, D1).** This module imports the standard
library and `serpent` at load. `serpent.emitter` and `serpent.spec` -- and
through them `stellar_sdk`, the `spec` extra -- are imported INSIDE `build`
and `inspect`, so `doctor` runs on the barest install and can say why `build`
would not. `tests/unit/test_core_zero_dep.py` keeps this module reachable from
nothing in `serpent/__init__.py`.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import json
import shutil
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Literal

import serpent
from serpent import _pins
from serpent._host import DEFAULT_TARGET_PROTOCOL

EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3

#: Every `--help` renders at this width, never the terminal's, so the goldens
#: under `tests/goldens/cli/` are stable.
HELP_WIDTH = 80

SPEC_EXTRA_HINT = (
    "install the `spec` extra: "
    'uv tool install "serpent[spec] @ git+https://github.com/ElliotFriend/stellar-serpent-sdk"'
)
WASM_TOOLS_HINT = (
    f"install wasm-tools {_pins.WASM_TOOLS_PIN} "
    "(https://github.com/bytecodealliance/wasm-tools/releases); optional, but CI runs it"
)
STELLAR_CLI_HINT = "install the Stellar CLI (https://developers.stellar.org/docs/tools/cli)"
PLUGIN_PATH_HINT = (
    "`stellar serpent ...` looks for `stellar-serpent` on PATH; "
    "put the `uv tool` bin directory on PATH (`uv tool update-shell`)"
)
HOST_HINT_FALLBACK = (
    "VIRTUAL_ENV=$PWD/.venv uvx maturin develop --release --manifest-path host/Cargo.toml"
)
NETWORK_RPC = {
    "testnet": "https://soroban-testnet.stellar.org",
    "mainnet": "https://mainnet.sorobanrpc.com",
}

Status = Literal["ok", "warn", "fail", "info"]


@dataclass(frozen=True)
class Check:
    """One `doctor` row: `status` decides the exit code, `remedy` is what to do."""

    name: str
    status: Status
    detail: str
    remedy: str = ""


@dataclass(frozen=True)
class Probes:
    """The seam between `run_doctor` and this machine, so tests script every
    answer instead of depending on what is installed here."""

    which: Callable[[str], str | None] = shutil.which
    import_module: Callable[[str], ModuleType] = importlib.import_module
    run: Callable[[list[str]], str] = dataclasses.field(default=lambda argv: _run(argv))
    python_version: tuple[int, int, int] = tuple(sys.version_info[:3])  # type: ignore[assignment]


def _run(argv: list[str]) -> str:
    """stdout of `argv`, or "" when it cannot run -- `doctor` never crashes on a tool."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout


def _fetch_version_info(url: str) -> dict[str, Any]:
    """One read-only `getVersionInfo` JSON-RPC call (ruling E4's `--network`)."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getVersionInfo"}).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 -- fixed https hosts
        payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
    result: dict[str, Any] = payload.get("result", {})
    return result


# --- doctor ------------------------------------------------------------------------


def run_doctor(
    probes: Probes,
    network: str | None,
    fetch_version_info: Callable[[str], dict[str, Any]] = _fetch_version_info,
) -> list[Check]:
    """The rows, in the order `doctor` prints them (G dossier D.1 / ruling E4).

    `fail` is reserved for the two states that make `build` impossible
    (Python too old, the `spec` extra absent); everything else warns or informs.
    """
    checks: list[Check] = []
    major, minor, micro = probes.python_version
    if (major, minor) >= (3, 11):
        checks.append(Check("python", "ok", f"{major}.{minor}.{micro}"))
    else:
        checks.append(
            Check("python", "fail", f"{major}.{minor}.{micro}", "serpent needs Python 3.11 or newer")
        )
    checks.append(Check("serpent", "ok", serpent.__version__))

    try:
        sdk = probes.import_module("stellar_sdk")
    except ImportError:
        checks.append(Check("spec extra", "fail", "stellar_sdk not importable", SPEC_EXTRA_HINT))
    else:
        version = str(getattr(sdk, "__version__", "?"))
        sdk_major = version.split(".", 1)[0]
        if sdk_major == "15":
            checks.append(Check("spec extra", "ok", f"stellar_sdk {version}"))
        else:
            checks.append(
                Check(
                    "spec extra",
                    "warn",
                    f"stellar_sdk {version} is outside the supported range >=15,<16",
                    SPEC_EXTRA_HINT,
                )
            )

    tool = probes.which("wasm-tools")
    if tool is None:
        checks.append(Check("wasm-tools", "warn", "not on PATH", WASM_TOOLS_HINT))
    else:
        reported = probes.run([tool, "--version"]).split()
        local = reported[1] if len(reported) >= 2 else "?"
        if local == _pins.WASM_TOOLS_PIN:
            checks.append(Check("wasm-tools", "ok", f"{local} (the CI pin)"))
        else:
            checks.append(
                Check(
                    "wasm-tools",
                    "warn",
                    f"local {local} differs from the CI pin {_pins.WASM_TOOLS_PIN}",
                    WASM_TOOLS_HINT,
                )
            )

    stellar = probes.which("stellar")
    if stellar is None:
        checks.append(Check("stellar CLI", "warn", "not on PATH", STELLAR_CLI_HINT))
    else:
        first_line = probes.run([stellar, "--version"]).splitlines()[:1]
        checks.append(Check("stellar CLI", "ok", first_line[0] if first_line else stellar))

    plugin = probes.which("stellar-serpent")
    if plugin is None:
        checks.append(Check("plugin on PATH", "warn", "stellar-serpent not on PATH", PLUGIN_PATH_HINT))
    else:
        checks.append(Check("plugin on PATH", "ok", plugin))

    try:
        probes.import_module("serpent_host")
    except ImportError:
        checks.append(Check("serpent_host", "info", "not built (needed only for real-host tests)", _rebuild_command()))
    else:
        checks.append(Check("serpent_host", "ok", "importable"))

    from serpent._host._codegen import PINNED_TAG  # stdlib-only module; deferred to keep load light

    checks.append(Check("env.json pin", "info", PINNED_TAG))
    checks.append(
        Check(
            "default target protocol",
            "info",
            f"{DEFAULT_TARGET_PROTOCOL} (mainnet's; `build --target-protocol N` opts higher)",
        )
    )

    if network is not None:
        url = NETWORK_RPC.get(network, network)
        try:
            info = fetch_version_info(url)
        except Exception as exc:  # noqa: BLE001 -- a doctor row, never a crash
            checks.append(Check("network", "warn", f"{url}: {exc}"))
        else:
            protocol = info.get("protocolVersion")
            status: Status = "info"
            detail = f"{url} protocol {protocol}"
            if isinstance(protocol, int) and protocol < DEFAULT_TARGET_PROTOCOL:
                status = "warn"
                detail += f" is below the default target {DEFAULT_TARGET_PROTOCOL}"
            checks.append(Check("network", status, detail))
    return checks


def _rebuild_command() -> str:
    try:
        from serpent.testing._marker import REBUILD_COMMAND
    except ImportError:
        return HOST_HINT_FALLBACK
    return str(REBUILD_COMMAND)


def exit_code_for(checks: Sequence[Check]) -> int:
    return EXIT_ENVIRONMENT if any(c.status == "fail" for c in checks) else EXIT_OK


def _cmd_doctor(args: argparse.Namespace) -> int:
    checks = run_doctor(Probes(), args.network)
    if args.json:
        print(json.dumps([dataclasses.asdict(c) for c in checks], indent=2))
    else:
        width = max(len(c.name) for c in checks)
        for check in checks:
            print(f"[{check.status:<4}] {check.name:<{width}}  {check.detail}")
            if check.remedy and check.status in ("warn", "fail", "info"):
                print(f"{'':<{width + 8}}-> {check.remedy}")
    return exit_code_for(checks)


# --- build / inspect (bodies land in Tasks 2 and 3) -------------------------------------


def _cmd_build(args: argparse.Namespace) -> int:
    print("stellar-serpent build is not yet implemented", file=sys.stderr)
    return EXIT_ENVIRONMENT


def _cmd_inspect(args: argparse.Namespace) -> int:
    print("stellar-serpent inspect is not yet implemented", file=sys.stderr)
    return EXIT_ENVIRONMENT


# --- the parser -----------------------------------------------------------------------


def _formatter(prog: str) -> argparse.HelpFormatter:
    return argparse.RawDescriptionHelpFormatter(prog, width=HELP_WIDTH)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stellar-serpent",
        description=(
            "Write Soroban smart contracts in Python. Invoked as `stellar serpent <command>` "
            "through the Stellar CLI's plugin discovery, or directly."
        ),
        formatter_class=_formatter,
    )
    parser.add_argument("--version", action="version", version=f"serpent {serpent.__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    build = sub.add_parser(
        "build",
        help="compile a contract module to a deployable .wasm",
        description="Compile PATH (a serpent contract module) and write the validated .wasm.",
        formatter_class=_formatter,
    )
    build.add_argument("contract", metavar="PATH", help="the contract module (.py)")
    build.add_argument("--out", metavar="FILE", help="output path (default: PATH with .wasm)")
    build.add_argument(
        "--meta",
        metavar="KEY=VALUE",
        action="append",
        default=[],
        help="a contractmetav0 pair; repeatable",
    )
    build.add_argument("--version", dest="contract_version", metavar="STR", help="the contractmetav0 `version` entry")
    build.add_argument(
        "--target-protocol",
        type=int,
        metavar="N",
        help="declare exactly protocol N; a host function gated above N is a compile error",
    )
    external = build.add_mutually_exclusive_group()
    external.add_argument(
        "--require-external-validate",
        action="store_true",
        help="fail if wasm-tools is not installed (default: run it when present)",
    )
    external.add_argument(
        "--no-external-validate", action="store_true", help="skip wasm-tools even when present"
    )
    build.add_argument("--json", action="store_true", help="print the build facts as JSON")
    build.add_argument("--quiet", action="store_true", help="print nothing on success")
    build.set_defaults(run=_cmd_build)

    inspect_ = sub.add_parser(
        "inspect",
        help="the sections, imports, and protocol facts of a built .wasm",
        description=(
            "Read a built .wasm: its sections, sha256 (the on-chain wasm hash), host-function "
            "imports with their protocol gates, exports, the DECLARED protocol against the floor "
            "RECOMPUTED from the imports, and the spec/meta entries by name. For the rendered "
            "interface use `stellar contract info interface --wasm FILE`."
        ),
        formatter_class=_formatter,
    )
    inspect_.add_argument("artifact", metavar="FILE", help="the .wasm to read")
    inspect_.add_argument("--wat", action="store_true", help="append a WAT-style disassembly")
    inspect_.add_argument("--json", action="store_true", help="print the facts as JSON")
    inspect_.set_defaults(run=_cmd_inspect)

    doctor = sub.add_parser(
        "doctor",
        help="check the toolchain: Python, the spec extra, wasm-tools, the Stellar CLI",
        description="Offline toolchain checks, one row each, with a remedy where a row is not ok.",
        formatter_class=_formatter,
    )
    doctor.add_argument(
        "--network",
        metavar="NAME|URL",
        help="also ask one RPC endpoint (testnet, mainnet, or a URL) for its protocol",
    )
    doctor.add_argument("--json", action="store_true", help="print the rows as JSON")
    doctor.set_defaults(run=_cmd_doctor)
    return parser


def subparser(name: str) -> argparse.ArgumentParser:
    """The registered subcommand parser, for its `--help` golden."""
    parser = build_parser()
    for action in parser._actions:  # noqa: SLF001 -- argparse offers no public accessor
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return action.choices[name]
    raise KeyError(name)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run: Callable[[argparse.Namespace], int] = args.run
    return run(args)


if __name__ == "__main__":  # pragma: no cover -- `python -m serpent.cli`
    raise SystemExit(main())
```

Notes for the implementer: (a) `Probes.python_version`'s default needs a `field(default_factory=...)` rather than the tuple-slice expression if mypy objects; the intent is "this interpreter's (major, minor, micro)". (b) `serpent._host._codegen` is imported inside `run_doctor` only to keep the module's load path short — confirm with `python -X importtime -c "import serpent.cli"` that `stellar_sdk` never appears. (c) The mypy `--strict` gate covers `src/`; add narrow `# type: ignore[...]` only where argparse's private types force it, and say so in a comment.

- [ ] **Step 5: `[project.scripts]` and the venv resync**

In `pyproject.toml`, after `dependencies = []`, add:

```toml
# `stellar-serpent` is the Stellar CLI plugin name (spec §9): `stellar serpent
# build x.py` runs whatever `stellar-serpent` is on PATH with the rest of the
# argv (verified against stellar-cli 27.1.0 in the G dossier, K4). The script
# itself is zero-dep; `build`/`inspect` need the `spec` extra and say so.
[project.scripts]
stellar-serpent = "serpent.cli:main"
```

Then re-sync the editable install WITHOUT pruning the host extension: `uv sync --all-groups --inexact` (keeps `serpent_host`; verify with `uv run --no-sync python -c "import serpent_host"` — if it is gone, rebuild with the maturin command from `docs/testing.md`). Confirm `.venv/bin/stellar-serpent --help` prints the three subcommands.

- [ ] **Step 6: Generate the goldens, run the tests**

Run: `SERPENT_REGEN_GOLDENS=1 uv run --no-sync pytest -q tests/unit/test_cli.py -k golden` → writes `tests/goldens/cli/root.help.txt` and `doctor.help.txt`. Read both; they must mention all three subcommands (root) and `--network`/`--json` (doctor).

Run: `uv run --no-sync pytest -q tests/unit/test_cli.py tests/unit/test_pins.py tests/unit/test_core_zero_dep.py` → all pass.

- [ ] **Step 7: Gates and commit**

Run the four gates (Global Constraints). Expected: ruff clean, format clean, mypy clean, suite = baseline + the new tests.

```bash
git add src/serpent/_pins.py src/serpent/cli.py pyproject.toml uv.lock tests/unit/test_cli.py tests/unit/test_pins.py tests/unit/test_core_zero_dep.py tests/goldens/cli/
git commit -m "feat(cli): add the stellar-serpent console script with doctor

The Stellar CLI dispatches stellar serpent <args> to a stellar-serpent
executable on PATH; this registers one whose doctor subcommand reports
the toolchain offline (Python, the spec extra, wasm-tools against the
shared pin, the Stellar CLI, plugin discoverability, the host extension)
with a remedy per row. build and inspect are registered and land next."
```

---

### Task 2: `stellar-serpent build`

**Files:**
- Modify: `src/serpent/cli.py` (`_cmd_build`, `_parse_meta`), `tests/unit/test_cli.py`
- Create: `tests/goldens/cli/build.help.txt`
- Test: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: `serpent.emitter.build_file(path, *, target_protocol, meta, version, validate_external) -> BuildResult` (fields `wasm`, `declared_protocol`, `target_protocol`, `exports`, `imports`, `runtime_parts_linked`, `needs_memory`, `module_size`); `serpent.compiler.CompileError.render(source_lines)`; `serpent.compiler.loader.CompilerBugError` (NOT caught).
- Produces: `serpent.cli.build_facts(result: BuildResult, *, source: Path, out: Path, external: str) -> dict[str, object]` (the JSON object AND the source of the human build line; keys: `source`, `out`, `bytes`, `sha256`, `declared_protocol`, `target_protocol`, `imports`, `exports`, `runtime_parts`, `memory`, `wasm_tools`).

- [ ] **Step 1: Failing tests**

Append to `tests/unit/test_cli.py`:

```python
# --- build ----------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = _ROOT / "examples"


def test_build_help_golden() -> None:
    golden("build", cli.subparser("build").format_help())


def test_build_writes_the_same_bytes_build_file_returns(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from serpent.emitter import build_file

    out = tmp_path / "counter.wasm"
    code = cli.main(["build", str(EXAMPLES / "counter.py"), "--out", str(out)])
    assert code == cli.EXIT_OK
    expected = build_file(EXAMPLES / "counter.py").wasm
    assert out.read_bytes() == expected
    printed = capsys.readouterr().out
    import hashlib

    assert hashlib.sha256(expected).hexdigest() in printed
    assert "declared protocol" in printed


def test_build_default_out_is_beside_the_source(tmp_path: Path) -> None:
    source = tmp_path / "c.py"
    source.write_text((EXAMPLES / "counter.py").read_text(encoding="utf-8"), encoding="utf-8")
    assert cli.main(["build", str(source), "--quiet"]) == cli.EXIT_OK
    assert (tmp_path / "c.wasm").exists()


def test_build_json_carries_the_facts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "e.wasm"
    assert cli.main(["build", str(EXAMPLES / "errors.py"), "--out", str(out), "--json"]) == cli.EXIT_OK
    facts = json.loads(capsys.readouterr().out)
    assert facts["declared_protocol"] == 22  # a constructor-bearing example (D9)
    assert facts["target_protocol"] is None
    assert facts["bytes"] == out.stat().st_size
    assert sorted(facts) == sorted(
        ["source", "out", "bytes", "sha256", "declared_protocol", "target_protocol",
         "imports", "exports", "runtime_parts", "memory", "wasm_tools"]
    )


def test_build_rejection_renders_diagnostics_and_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text(
        "from serpent import Env, U32, contract\n\n\n@contract\nclass C:\n"
        "    def f(self, env: Env, x: U32) -> U32:\n        return x + 1.5\n",
        encoding="utf-8",
    )
    assert cli.main(["build", str(bad), "--out", str(tmp_path / "bad.wasm")]) == cli.EXIT_REJECTED
    err = capsys.readouterr().err
    assert "error[SPT" in err
    assert not (tmp_path / "bad.wasm").exists()


def test_build_missing_source_is_an_environment_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["build", str(tmp_path / "nope.py")]) == cli.EXIT_ENVIRONMENT
    assert "nope.py" in capsys.readouterr().err


def test_build_meta_pairs_land_in_contractmetav0(tmp_path: Path) -> None:
    from serpent.spec.decode import decode_meta
    from tests.unit.test_sections import _wasm_custom_section

    out = tmp_path / "m.wasm"
    assert cli.main(["build", str(EXAMPLES / "counter.py"), "--out", str(out), "--meta", "team=devrel", "--meta", "tag=v1", "--quiet"]) == cli.EXIT_OK
    pairs = dict(decode_meta(_wasm_custom_section(out.read_bytes(), "contractmetav0")))
    assert pairs["team"] == "devrel" and pairs["tag"] == "v1"


def test_build_reserved_meta_key_is_an_environment_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["build", str(EXAMPLES / "counter.py"), "--out", str(tmp_path / "x.wasm"), "--meta", "serpentver=9"]) == cli.EXIT_ENVIRONMENT
    assert "reserved" in capsys.readouterr().err


def test_build_meta_without_equals_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["build", "x.py", "--meta", "novalue"])
    assert info.value.code == cli.EXIT_USAGE


def test_build_target_protocol_below_a_constructor_floor_is_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["build", str(EXAMPLES / "errors.py"), "--out", str(tmp_path / "e.wasm"), "--target-protocol", "21"])
    assert code == cli.EXIT_REJECTED
    assert "SPT6001" in capsys.readouterr().err


def test_build_require_external_validate_without_the_tool_is_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    code = cli.main(["build", str(EXAMPLES / "counter.py"), "--out", str(tmp_path / "c.wasm"), "--require-external-validate"])
    assert code == cli.EXIT_ENVIRONMENT
    assert "wasm-tools" in capsys.readouterr().err


def test_build_without_the_spec_extra_names_the_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Simulate a bare install: make `stellar_sdk` unimportable for this call."""
    import builtins

    real_import = builtins.__import__

    def no_sdk(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "stellar_sdk" or name.startswith("stellar_sdk."):
            raise ModuleNotFoundError(name, name=name)
        return real_import(name, *args, **kwargs)

    for cached in [m for m in sys.modules if m == "stellar_sdk" or m.startswith(("stellar_sdk.", "serpent.emitter", "serpent.spec"))]:
        monkeypatch.delitem(sys.modules, cached)
    monkeypatch.setattr(builtins, "__import__", no_sdk)
    code = cli.main(["build", str(EXAMPLES / "counter.py"), "--out", str(tmp_path / "c.wasm")])
    assert code == cli.EXIT_ENVIRONMENT
    assert cli.SPEC_EXTRA_HINT in capsys.readouterr().err
```

The `decode_meta` import in `test_build_meta_pairs_land_in_contractmetav0` lands in Task 3; until then, mark that one test `@pytest.mark.skip(reason="serpent.spec.decode lands in Task 3")` and REMOVE the skip in Task 3, Step 4.

Run: `uv run --no-sync pytest -q tests/unit/test_cli.py -k build` → FAIL (stub returns 3; no golden).

- [ ] **Step 2: Implement `_cmd_build`**

Replace the stub in `src/serpent/cli.py`:

```python
def _parse_meta(pair: str) -> tuple[str, str]:
    """`KEY=VALUE` for `--meta`; anything else is a usage error (exit 2)."""
    key, sep, value = pair.partition("=")
    if not sep or not key:
        raise argparse.ArgumentTypeError(f"--meta wants KEY=VALUE, got {pair!r}")
    return key, value


def build_facts(result: Any, *, source: Path, out: Path, external: str) -> dict[str, object]:
    """The facts `build` prints (human and `--json`), from one `BuildResult`.

    `sha256` is the on-chain wasm hash (ruling D-E7: byte-reproducible output
    makes it user-verifiable); `declared_protocol` is what `contractenvmetav0`
    carries and `target_protocol` the requested gate or `None` (ruling E9/D6).
    """
    return {
        "source": str(source),
        "out": str(out),
        "bytes": result.module_size,
        "sha256": hashlib.sha256(result.wasm).hexdigest(),
        "declared_protocol": result.declared_protocol,
        "target_protocol": result.target_protocol,
        "imports": list(result.imports),
        "exports": list(result.exports),
        "runtime_parts": sorted(result.runtime_parts_linked),
        "memory": result.needs_memory,
        "wasm_tools": external,
    }


def _print_build(facts: dict[str, object]) -> None:
    target = facts["target_protocol"]
    declared = (
        f"{facts['declared_protocol']} (computed floor)"
        if target is None
        else f"{facts['declared_protocol']} (requested target {target})"
    )
    parts = facts["runtime_parts"]
    imports = facts["imports"]
    assert isinstance(parts, list) and isinstance(imports, list)
    print(f"built {facts['source']} -> {facts['out']}")
    print(f"  bytes             : {facts['bytes']}")
    print(f"  sha256            : {facts['sha256']}  (the on-chain wasm hash)")
    print(f"  declared protocol : {declared}")
    print(f"  imports           : {len(imports)} host function(s)")
    print(f"  runtime parts     : {', '.join(parts) if parts else 'none'}")
    print(f"  memory            : {'yes' if facts['memory'] else 'no'}")
    print(f"  wasm-tools        : {facts['wasm_tools']}")


def _cmd_build(args: argparse.Namespace) -> int:
    try:
        from serpent.compiler.diagnostics import CompileError
        from serpent.emitter import build_file
    except ModuleNotFoundError as exc:
        if (exc.name or "").split(".", 1)[0] != "stellar_sdk":
            raise
        print(f"stellar-serpent build needs stellar_sdk; {SPEC_EXTRA_HINT}", file=sys.stderr)
        return EXIT_ENVIRONMENT

    source = Path(args.contract)
    if not source.is_file():
        print(f"no such contract module: {source}", file=sys.stderr)
        return EXIT_ENVIRONMENT
    out = Path(args.out) if args.out else source.with_suffix(".wasm")
    try:
        meta = dict(_parse_meta(pair) for pair in args.meta)
    except argparse.ArgumentTypeError as exc:
        build_parser().error(str(exc))  # exits 2

    validate_external: bool | None = None
    if args.require_external_validate:
        validate_external = True
    elif args.no_external_validate:
        validate_external = False

    try:
        result = build_file(
            source,
            target_protocol=args.target_protocol,
            meta=meta,
            version=args.contract_version,
            validate_external=validate_external,
        )
    except CompileError as exc:
        lines = source.read_text(encoding="utf-8").splitlines()
        print(exc.render(lines), file=sys.stderr)
        return EXIT_REJECTED
    except ValueError as exc:  # a reserved --meta key, before assembly
        print(str(exc), file=sys.stderr)
        return EXIT_ENVIRONMENT
    except RuntimeError as exc:  # --require-external-validate with no wasm-tools
        print(str(exc), file=sys.stderr)
        return EXIT_ENVIRONMENT

    try:
        out.write_bytes(result.wasm)
    except OSError as exc:
        print(f"cannot write {out}: {exc}", file=sys.stderr)
        return EXIT_ENVIRONMENT

    if validate_external is False:
        external = "skipped (--no-external-validate)"
    elif shutil.which("wasm-tools") is None:
        external = "not installed (skipped)"
    else:
        external = "validated"
    facts = build_facts(result, source=source, out=out, external=external)
    if args.json:
        print(json.dumps(facts, indent=2))
    elif not args.quiet:
        _print_build(facts)
    return EXIT_OK
```

Add `import hashlib` and `from pathlib import Path` to the module imports. Register `--meta` with `type=_parse_meta` (in Task 1's `build_parser`, change that argument to `type=_parse_meta`), so argparse itself turns a malformed pair into a usage error (exit 2) and `args.meta` is already a list of `(key, value)` tuples; in `_cmd_build` the try/except around `_parse_meta` above is therefore replaced by the single line `meta = dict(args.meta)`.

Note `CompileError` also covers `ProtocolGateError`-derived SPT6001 rejects (frontend raises a located `CompileError`), and `CompilerBugError` (an `AssertionError`) is deliberately not caught.

- [ ] **Step 3: Golden, tests, gates, commit**

Run: `SERPENT_REGEN_GOLDENS=1 uv run --no-sync pytest -q tests/unit/test_cli.py -k build_help` then `uv run --no-sync pytest -q tests/unit/test_cli.py` → all pass (one skip for the Task 3 test). Four gates.

```bash
git add src/serpent/cli.py tests/unit/test_cli.py tests/goldens/cli/build.help.txt
git commit -m "feat(cli): add stellar-serpent build

Compiles a contract module with build_file and writes the validated
wasm beside the source or at --out, printing the bytes, the sha256 (the
on-chain wasm hash), the declared protocol against the requested target,
the imports, and the linked runtime parts. A rejected contract prints its
rendered diagnostics and exits 1; environment problems exit 3."
```

---

### Task 3: `serpent.spec.decode`, `serpent.emitter.artifact`, and `stellar-serpent inspect`

**Files:**
- Create: `src/serpent/spec/decode.py`, `src/serpent/emitter/artifact.py`, `tests/unit/test_spec_decode.py`, `tests/unit/test_artifact.py`, `tests/goldens/cli/inspect.help.txt`
- Modify: `src/serpent/spec/__init__.py` (re-exports), `src/serpent/cli.py` (`_cmd_inspect`), `tests/unit/test_cli.py`
- Test: the three test modules

**Interfaces:**
- Consumes: `serpent.emitter.validate.iter_sections(wasm) -> Iterator[tuple[int, bytes]]`, `read_name(data, i) -> tuple[str, int]`; `serpent.emitter.printer._decode_imports(payload) -> list[tuple[str, str, int]]`, `_decode_exports(payload) -> list[tuple[str, int, int]]`, `disassemble(wasm) -> str`; `serpent._host.HOST_FUNCTIONS` (`HostFn.name/module/export/min_protocol`), `compute_protocol_floor`, `CONSTRUCTOR_MIN_PROTOCOL`; `serpent.emitter.sections.{ENV_META,SPEC,META}_SECTION_NAME`; `stellar_sdk.xdr.{SCEnvMetaEntry,SCSpecEntry,SCMetaEntry}`, `xdrlib3.Unpacker` (the `tests/unit/test_sections.py:207-222` decoders, moved into the package).
- Produces: `serpent.spec.decode.decode_env_meta(payload: bytes) -> int`; `decode_spec_entries(payload: bytes) -> list[xdr.SCSpecEntry]`; `decode_meta(payload: bytes) -> list[tuple[str, str]]`; `spec_entry_names_by_kind(entries) -> dict[str, list[str]]` with keys exactly `("functions", "structs", "unions", "enums", "error_enums", "events")` in that order; `serpent.emitter.artifact.inspect_artifact(wasm: bytes) -> Artifact` with `Artifact(sha256, size, sections: tuple[Section, ...], imports: tuple[Import, ...], exports: tuple[str, ...], has_constructor: bool, declared_protocol: int | None, recomputed_protocol: int, spec: dict[str, list[str]] | None, meta: list[tuple[str, str]] | None)`, `Section(id: int, name: str | None, size: int)`, `Import(module: str, field: str, host_fn: str | None, min_protocol: int | None)`; `Artifact.protocol_mismatch` property (`declared_protocol is not None and declared_protocol != recomputed_protocol`); `serpent.emitter.artifact.MalformedArtifact(ValueError)`.

- [ ] **Step 1: `decode.py` tests (failing first)**

Create `tests/unit/test_spec_decode.py`:

```python
"""`serpent.spec.decode`: the read direction of the three custom sections (G dossier C3)."""

from __future__ import annotations

from serpent import U32, Env, Symbol, contract, contracterror, contracttype, errorcode
from serpent.spec import build_env_meta, build_meta, build_spec_entries
from serpent.spec.decode import (
    decode_env_meta,
    decode_meta,
    decode_spec_entries,
    spec_entry_names_by_kind,
)


@contracttype
class Point:
    x: U32


@contracterror
class Err:
    Bad = errorcode(1)


@contract
class Sample:
    def get(self, env: Env, p: Point) -> U32:
        return p.x


def test_env_meta_round_trips() -> None:
    assert decode_env_meta(build_env_meta(28)) == 28


def test_meta_round_trips_in_order() -> None:
    payload = build_meta("Sample", "1.2", {"team": "devrel"})
    pairs = decode_meta(payload)
    assert ("name", "Sample") in pairs
    assert ("version", "1.2") in pairs
    assert pairs[-1] == ("team", "devrel")
    assert dict(pairs)["serpentver"]


def test_spec_entries_round_trip_and_group_by_kind() -> None:
    payload = build_spec_entries(Sample, types=(Point, Err))
    entries = decode_spec_entries(payload)
    assert len(entries) == 3
    by_kind = spec_entry_names_by_kind(entries)
    assert list(by_kind) == ["functions", "structs", "unions", "enums", "error_enums", "events"]
    assert by_kind["functions"] == ["get"]
    assert by_kind["structs"] == ["Point"]
    assert by_kind["error_enums"] == ["Err"]
    assert by_kind["unions"] == by_kind["enums"] == by_kind["events"] == []
```

Check `build_spec_entries`'s actual keyword for the type inventory against `src/serpent/spec/sections.py:173` before running (the dossier says "unions/enums travel in the existing `types=` inventory"; use the real parameter name). Run → FAIL (`serpent.spec.decode` missing).

- [ ] **Step 2: Implement `decode.py`**

Create `src/serpent/spec/decode.py`:

```python
"""Decode the three Soroban custom sections serpent emits (G dossier C3; ruling E3).

The READ direction of `serpent.spec.sections`: `build_env_meta` /
`build_spec_entries` / `build_meta` produce the payloads, and these three
functions take them back apart through the same `stellar_sdk` XDR classes, so
there is one definition of each section's shape in this package and none in
the CLI (spec §10's one-codec rule, applied to XDR). `tests/unit/test_sections.py`
used to carry private copies of the two stream decoders; they moved here.

A `contractspecv0` payload is a bare STREAM of `SCSpecEntry` with no count
prefix, and `contractmetav0` a bare stream of `SCMetaEntry` -- hence the
`Unpacker` loops rather than a single `from_xdr_bytes`.
"""

from __future__ import annotations

from stellar_sdk import xdr
from xdrlib3 import Unpacker

#: The kind order `spec_entry_names_by_kind` reports, and the human names
#: `stellar-serpent inspect` prints -- the XDR kind order ruling E7 fixed
#: for emission, functions first because that is what a reader looks for.
KIND_ORDER: tuple[str, ...] = ("functions", "structs", "unions", "enums", "error_enums", "events")


def decode_env_meta(payload: bytes) -> int:
    """The declared protocol out of a `contractenvmetav0` payload."""
    entry = xdr.SCEnvMetaEntry.from_xdr_bytes(payload)
    if entry.interface_version is None:
        raise ValueError("contractenvmetav0 entry carries no interface version")
    return int(entry.interface_version.protocol.uint32)


def decode_spec_entries(payload: bytes) -> list[xdr.SCSpecEntry]:
    """Every `SCSpecEntry` in a `contractspecv0` payload, in stream order."""
    unpacker = Unpacker(payload)
    entries: list[xdr.SCSpecEntry] = []
    while unpacker.get_position() < len(payload):
        entries.append(xdr.SCSpecEntry.unpack(unpacker))
    return entries


def decode_meta(payload: bytes) -> list[tuple[str, str]]:
    """Every `(key, value)` in a `contractmetav0` payload, in stream order."""
    unpacker = Unpacker(payload)
    pairs: list[tuple[str, str]] = []
    while unpacker.get_position() < len(payload):
        entry = xdr.SCMetaEntry.unpack(unpacker)
        if entry.v0 is None:
            raise ValueError("contractmetav0 entry is not a v0 pair")
        pairs.append((entry.v0.key.decode("utf-8"), entry.v0.val.decode("utf-8")))
    return pairs


def _name_of(entry: xdr.SCSpecEntry) -> tuple[str, str]:
    """`(kind, name)` for one entry; the field names are stellar_sdk's generated ones."""
    kind = entry.kind
    if kind == xdr.SCSpecEntryKind.SC_SPEC_ENTRY_FUNCTION_V0 and entry.function_v0 is not None:
        return "functions", entry.function_v0.name.sc_symbol.decode("utf-8")
    if kind == xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_STRUCT_V0 and entry.udt_struct_v0 is not None:
        return "structs", entry.udt_struct_v0.name.decode("utf-8")
    if kind == xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_UNION_V0 and entry.udt_union_v0 is not None:
        return "unions", entry.udt_union_v0.name.decode("utf-8")
    if kind == xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_ENUM_V0 and entry.udt_enum_v0 is not None:
        return "enums", entry.udt_enum_v0.name.decode("utf-8")
    if (
        kind == xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_ERROR_ENUM_V0
        and entry.udt_error_enum_v0 is not None
    ):
        return "error_enums", entry.udt_error_enum_v0.name.decode("utf-8")
    if kind == xdr.SCSpecEntryKind.SC_SPEC_ENTRY_EVENT_V0 and entry.event_v0 is not None:
        return "events", entry.event_v0.name.sc_symbol.decode("utf-8")
    raise ValueError(f"unrecognised SCSpecEntry kind {kind!r}")


def spec_entry_names_by_kind(entries: list[xdr.SCSpecEntry]) -> dict[str, list[str]]:
    """Names grouped under `KIND_ORDER`'s keys, every key present (possibly empty)."""
    grouped: dict[str, list[str]] = {kind: [] for kind in KIND_ORDER}
    for entry in entries:
        kind, name = _name_of(entry)
        grouped[kind].append(name)
    return grouped
```

The exact attribute names (`function_v0.name.sc_symbol`, `udt_struct_v0.name` as `bytes`, `event_v0.name.sc_symbol`) must be confirmed against `stellar_sdk.xdr`'s generated classes; `tests/unit/test_sections.py` already reads these same fields — copy its spellings where they differ. Add `decode_env_meta`, `decode_meta`, `decode_spec_entries`, `spec_entry_names_by_kind` to `serpent/spec/__init__.py`'s imports and `__all__`. Then REPLACE `tests/unit/test_sections.py`'s private `_unpack`/`_unpack_meta` bodies with one-line delegations to `decode_spec_entries`/`decode_meta` (keep the names — `test_examples.py` imports `_unpack`).

Run: `uv run --no-sync pytest -q tests/unit/test_spec_decode.py tests/unit/test_sections.py` → pass.

- [ ] **Step 3: `artifact.py` tests (failing first)**

Create `tests/unit/test_artifact.py`:

```python
"""`inspect_artifact`: the facts about a built module the stock CLI cannot know (ruling E3, F.1.2)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from serpent._host import CONSTRUCTOR_MIN_PROTOCOL, HOST_FUNCTIONS
from serpent.emitter import build_file
from serpent.emitter.artifact import MalformedArtifact, inspect_artifact
from tests.unit.test_emitter_end_to_end import EXAMPLES

DEPLOYED_SHAPES = (
    Path(__file__).resolve().parents[1] / "real_host" / "fixtures" / "testnet" / "shapes" / "deployed.wasm"
)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_declared_equals_recomputed_for_every_example(path: Path) -> None:
    """F.1.2: the floor RE-DERIVED from the artifact's imports (+ the D9
    constructor gate) must equal what the compiler declared."""
    built = build_file(path)
    art = inspect_artifact(built.wasm)
    assert art.declared_protocol == built.declared_protocol
    assert art.recomputed_protocol == built.declared_protocol
    assert not art.protocol_mismatch
    assert art.has_constructor == ("__constructor" in built.exports)
    assert art.sha256 == hashlib.sha256(built.wasm).hexdigest()
    assert art.size == len(built.wasm)
    assert set(art.exports) == set(built.exports)
    assert {i.host_fn for i in art.imports} == set(built.imports)


def test_the_deployed_shapes_bytes_declare_20_and_recompute_20() -> None:
    art = inspect_artifact(DEPLOYED_SHAPES.read_bytes())
    assert art.declared_protocol == 20 and art.recomputed_protocol == 20
    assert not art.has_constructor
    assert art.spec is not None and "area" in art.spec["functions"]
    assert art.spec["unions"] == ["Shape"] and art.spec["enums"] == ["Color"]
    assert art.meta is not None and dict(art.meta)["name"] == "Drawing"


def test_a_constructor_export_raises_the_recomputed_floor_to_22() -> None:
    errors = next(p for p in EXAMPLES if p.stem == "errors")
    art = inspect_artifact(build_file(errors).wasm)
    assert art.has_constructor and art.recomputed_protocol == CONSTRUCTOR_MIN_PROTOCOL == 22


def test_sections_are_listed_with_names_and_sizes() -> None:
    art = inspect_artifact(build_file(EXAMPLES[0]).wasm)
    names = [s.name for s in art.sections if s.id == 0]
    assert names == ["contractenvmetav0", "contractspecv0", "contractmetav0"]
    assert all(s.size > 0 for s in art.sections)


def test_imports_carry_their_protocol_gates() -> None:
    art = inspect_artifact(build_file(EXAMPLES[0]).wasm)
    by_name = {fn.name: fn for fn in HOST_FUNCTIONS}
    for imp in art.imports:
        assert imp.host_fn in by_name
        assert imp.min_protocol == by_name[imp.host_fn].min_protocol


def test_a_gated_import_recomputes_its_gate() -> None:
    """A hand-made module importing ONE host function gated above 20 (pick the
    lowest-gated `HostFn` with `min_protocol is not None` from the pin) must
    recompute exactly that gate; declared is None (no env-meta), so
    `protocol_mismatch` is False by definition."""
    gated = min((fn for fn in HOST_FUNCTIONS if fn.min_protocol is not None), key=lambda f: f.min_protocol or 0)
    wasm = _module_importing(gated.module, gated.export, params=len(gated.arg_types))
    art = inspect_artifact(wasm)
    assert art.declared_protocol is None
    assert art.recomputed_protocol == gated.min_protocol
    assert not art.protocol_mismatch


def test_a_mismatch_is_reported() -> None:
    """The same module, with a contractenvmetav0 declaring 20 spliced in."""
    from serpent.spec import build_env_meta

    gated = min((fn for fn in HOST_FUNCTIONS if fn.min_protocol is not None), key=lambda f: f.min_protocol or 0)
    wasm = _module_importing(gated.module, gated.export, params=len(gated.arg_types), env_meta=build_env_meta(20))
    art = inspect_artifact(wasm)
    assert art.declared_protocol == 20 and art.recomputed_protocol == gated.min_protocol
    assert art.protocol_mismatch


def test_not_a_wasm_module_is_malformed() -> None:
    with pytest.raises(MalformedArtifact):
        inspect_artifact(b"\x00asm\x01\x00\x00\x00truncated")
    with pytest.raises(MalformedArtifact):
        inspect_artifact(b"hello")


def _module_importing(module: str, field: str, *, params: int, env_meta: bytes | None = None) -> bytes:
    """The smallest valid module importing one i64^params -> i64 function.

    Hand-encoded with `serpent.emitter.encode` (LEB128 + section framing), so
    this test does not depend on the emitter's higher layers; see
    `tests/unit/test_emitter_validate.py` for the same hand-assembly idiom.
    """
    from serpent.emitter import encode

    functype = bytes([0x60, params, *([0x7E] * params), 0x01, 0x7E])
    type_section = encode.section(1, encode.uleb(1) + functype)
    import_entry = encode.name(module) + encode.name(field) + bytes([0x00]) + encode.uleb(0)
    import_section = encode.section(2, encode.uleb(1) + import_entry)
    custom = b""
    if env_meta is not None:
        custom = encode.section(0, encode.name("contractenvmetav0") + env_meta)
    return b"\x00asm\x01\x00\x00\x00" + type_section + import_section + custom
```

`encode.section`/`encode.uleb`/`encode.name` are the names to CONFIRM in `src/serpent/emitter/encode.py` (the module's docstring says "LEB128 + section framing"); use whatever it exports for "frame a section with id N", "uleb128", and "a wasm name (uleb length + utf-8)". Run → FAIL (`serpent.emitter.artifact` missing).

- [ ] **Step 4: Implement `artifact.py`**

Create `src/serpent/emitter/artifact.py`:

```python
"""`inspect_artifact`: what a built module says about itself (ruling E3; G dossier D.1).

The facts `stellar contract info` cannot know, because they are serpent's:
the host-function IMPORTS resolved to their pinned names and protocol gates,
the DECLARED protocol (out of `contractenvmetav0`) beside the floor RECOMPUTED
from those imports plus the constructor gate (rulings D6/D9) -- the
honest-declaration check -- and the spec/meta entries by name. The interface
itself is deliberately NOT rendered here; the stock CLI already does that
from the same section (spec §10's drift rule).

Everything is read with the decoders this package already has
(`validate.iter_sections`, `printer._decode_imports`/`_decode_exports`) and
`serpent.spec.decode` for the XDR. Nothing decodes a `Val` word.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from serpent._host import CONSTRUCTOR_MIN_PROTOCOL, HOST_FUNCTIONS, compute_protocol_floor
from serpent.emitter import printer, validate
from serpent.emitter.frame import EmitError
from serpent.emitter.sections import ENV_META_SECTION_NAME, META_SECTION_NAME, SPEC_SECTION_NAME
from serpent.spec import decode

_SECTION_NAMES = {
    1: "type", 2: "import", 3: "function", 4: "table", 5: "memory", 6: "global",
    7: "export", 8: "start", 9: "element", 10: "code", 11: "data", 12: "data count",
}
_BY_IMPORT = {(fn.module, fn.export): fn for fn in HOST_FUNCTIONS}


class MalformedArtifact(ValueError):
    """Not a wasm module we can read: bad magic, truncated, or an unreadable section."""


@dataclass(frozen=True)
class Section:
    id: int
    name: str | None  # the custom section's name for id 0, else the standard name
    size: int


@dataclass(frozen=True)
class Import:
    module: str
    field: str
    host_fn: str | None  # the pinned name, or None for an import the pin does not know
    min_protocol: int | None


@dataclass(frozen=True)
class Artifact:
    sha256: str
    size: int
    sections: tuple[Section, ...]
    imports: tuple[Import, ...]
    exports: tuple[str, ...]
    has_constructor: bool
    declared_protocol: int | None
    recomputed_protocol: int
    spec: dict[str, list[str]] | None
    meta: list[tuple[str, str]] | None

    @property
    def protocol_mismatch(self) -> bool:
        return self.declared_protocol is not None and self.declared_protocol != self.recomputed_protocol

    @property
    def unknown_imports(self) -> tuple[Import, ...]:
        return tuple(i for i in self.imports if i.host_fn is None)


def inspect_artifact(wasm: bytes) -> Artifact:
    try:
        raw = list(validate.iter_sections(wasm))
    except EmitError as exc:
        raise MalformedArtifact(str(exc)) from exc

    sections: list[Section] = []
    imports: list[Import] = []
    exports: list[str] = []
    declared: int | None = None
    spec: dict[str, list[str]] | None = None
    meta: list[tuple[str, str]] | None = None
    try:
        for sid, payload in raw:
            if sid == 0:
                name, start = validate.read_name(payload, 0)
                data = payload[start:]
                sections.append(Section(0, name, len(payload)))
                if name == ENV_META_SECTION_NAME:
                    declared = decode.decode_env_meta(data)
                elif name == SPEC_SECTION_NAME:
                    spec = decode.spec_entry_names_by_kind(decode.decode_spec_entries(data))
                elif name == META_SECTION_NAME:
                    meta = decode.decode_meta(data)
                continue
            sections.append(Section(sid, _SECTION_NAMES.get(sid), len(payload)))
            if sid == 2:
                for module, field, _typeidx in printer._decode_imports(payload):  # noqa: SLF001
                    fn = _BY_IMPORT.get((module, field))
                    imports.append(Import(module, field, fn.name if fn else None, fn.min_protocol if fn else None))
            elif sid == 7:
                exports.extend(name for name, kind, _idx in printer._decode_exports(payload) if kind == 0)  # noqa: SLF001
    except (EmitError, ValueError, IndexError) as exc:
        raise MalformedArtifact(f"unreadable section: {exc}") from exc

    known = [i.host_fn for i in imports if i.host_fn is not None]
    floor = compute_protocol_floor(known)
    has_constructor = "__constructor" in exports
    if has_constructor:
        floor = max(floor, CONSTRUCTOR_MIN_PROTOCOL)
    return Artifact(
        sha256=hashlib.sha256(wasm).hexdigest(),
        size=len(wasm),
        sections=tuple(sections),
        imports=tuple(imports),
        exports=tuple(exports),
        has_constructor=has_constructor,
        declared_protocol=declared,
        recomputed_protocol=floor,
        spec=spec,
        meta=meta,
    )
```

Confirm against `printer._decode_imports`/`_decode_exports`'s actual return shapes (`printer.py:225-267`) and whether `_decode_imports` yields only function imports. If the printer's helpers are too private to lean on, promote them to public names (`decode_imports`, `decode_exports`) in `printer.py` in this task and import those instead — note it in the commit body. Run `tests/unit/test_artifact.py` → pass.

- [ ] **Step 5: `_cmd_inspect`, its tests, the golden**

Append to `tests/unit/test_cli.py`:

```python
# --- inspect --------------------------------------------------------------------------


def test_inspect_help_golden() -> None:
    golden("inspect", cli.subparser("inspect").format_help())


def test_inspect_prints_declared_and_recomputed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "e.wasm"
    assert cli.main(["build", str(EXAMPLES / "errors.py"), "--out", str(out), "--quiet"]) == cli.EXIT_OK
    assert cli.main(["inspect", str(out)]) == cli.EXIT_OK
    text = capsys.readouterr().out
    assert "declared protocol  : 22" in text
    assert "recomputed floor   : 22" in text
    assert "MISMATCH" not in text
    assert "contractspecv0" in text and "functions" in text
    assert "stellar contract info interface" in text


def test_inspect_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "c.wasm"
    cli.main(["build", str(EXAMPLES / "counter.py"), "--out", str(out), "--quiet"])
    assert cli.main(["inspect", str(out), "--json"]) == cli.EXIT_OK
    facts = json.loads(capsys.readouterr().out)
    assert facts["declared_protocol"] == facts["recomputed_protocol"] == 20
    assert facts["protocol_mismatch"] is False
    assert isinstance(facts["imports"], list) and facts["imports"][0]["host_fn"]


def test_inspect_wat_appends_a_disassembly(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "c.wasm"
    cli.main(["build", str(EXAMPLES / "counter.py"), "--out", str(out), "--quiet"])
    assert cli.main(["inspect", str(out), "--wat"]) == cli.EXIT_OK
    assert "(func" in capsys.readouterr().out


def test_inspect_malformed_is_exit_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.wasm"
    bad.write_bytes(b"not wasm")
    assert cli.main(["inspect", str(bad)]) == cli.EXIT_REJECTED
    assert "not a wasm module" in capsys.readouterr().err


def test_inspect_missing_file_is_exit_3(tmp_path: Path) -> None:
    assert cli.main(["inspect", str(tmp_path / "nope.wasm")]) == cli.EXIT_ENVIRONMENT
```

Replace the `_cmd_inspect` stub:

```python
def _cmd_inspect(args: argparse.Namespace) -> int:
    try:
        from serpent.emitter.artifact import MalformedArtifact, inspect_artifact
        from serpent.emitter.printer import disassemble
    except ModuleNotFoundError as exc:
        if (exc.name or "").split(".", 1)[0] != "stellar_sdk":
            raise
        print(f"stellar-serpent inspect needs stellar_sdk; {SPEC_EXTRA_HINT}", file=sys.stderr)
        return EXIT_ENVIRONMENT
    path = Path(args.artifact)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return EXIT_ENVIRONMENT
    wasm = path.read_bytes()
    try:
        art = inspect_artifact(wasm)
    except MalformedArtifact as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        return EXIT_REJECTED

    if args.json:
        facts: dict[str, object] = {
            "file": str(path),
            "sha256": art.sha256,
            "bytes": art.size,
            "sections": [dataclasses.asdict(s) for s in art.sections],
            "imports": [dataclasses.asdict(i) for i in art.imports],
            "exports": list(art.exports),
            "has_constructor": art.has_constructor,
            "declared_protocol": art.declared_protocol,
            "recomputed_protocol": art.recomputed_protocol,
            "protocol_mismatch": art.protocol_mismatch,
            "spec": art.spec,
            "meta": art.meta,
        }
        if args.wat:
            facts["wat"] = disassemble(wasm)
        print(json.dumps(facts, indent=2))
        return EXIT_OK

    print(f"{path}  ({art.size} bytes)")
    print(f"  sha256             : {art.sha256}  (the on-chain wasm hash)")
    declared = "absent" if art.declared_protocol is None else str(art.declared_protocol)
    flag = "  MISMATCH: the declared protocol is not the floor the imports require" if art.protocol_mismatch else ""
    print(f"  declared protocol  : {declared}")
    print(f"  recomputed floor   : {art.recomputed_protocol}{' (constructor gate 22 applied)' if art.has_constructor else ''}{flag}")
    print("  sections           :")
    for section in art.sections:
        print(f"    {section.id:>2} {section.name or '?':<20} {section.size:>7} B")
    print(f"  imports            : {len(art.imports)}")
    for imp in art.imports:
        gate = "" if imp.min_protocol is None else f"  (protocol >= {imp.min_protocol})"
        print(f"    {imp.module}.{imp.field:<4} {imp.host_fn or 'UNKNOWN to the pinned env.json'}{gate}")
    print(f"  exports            : {', '.join(art.exports)}")
    if art.spec is not None:
        print("  contractspecv0     :")
        for kind, names in art.spec.items():
            if names:
                print(f"    {kind:<12} {', '.join(names)}")
        print("    (rendered interface: `stellar contract info interface --wasm FILE`)")
    if art.meta is not None:
        print("  contractmetav0     : " + ", ".join(f"{k}={v}" for k, v in art.meta))
    if args.wat:
        print()
        print(disassemble(wasm))
    return EXIT_OK
```

Remove the Task 2 skip on `test_build_meta_pairs_land_in_contractmetav0`. Regenerate the inspect golden; run `tests/unit/test_cli.py tests/unit/test_artifact.py tests/unit/test_spec_decode.py tests/unit/test_sections.py` → pass. Four gates.

- [ ] **Step 6: Commit**

```bash
git add src/serpent/spec/decode.py src/serpent/spec/__init__.py src/serpent/emitter/artifact.py src/serpent/emitter/printer.py src/serpent/cli.py tests/unit/test_spec_decode.py tests/unit/test_artifact.py tests/unit/test_sections.py tests/unit/test_cli.py tests/goldens/cli/inspect.help.txt
git commit -m "feat(cli): add stellar-serpent inspect with section and protocol decoding

serpent.spec.decode reads the three custom sections back through the
same stellar_sdk XDR classes that build them, and
serpent.emitter.artifact resolves a module's imports to the pinned host
functions and recomputes the protocol floor (imports plus the
constructor gate) beside the declared one. inspect prints both, flags a
mismatch, lists spec entries by kind, and points at the stock CLI for
the rendered interface."
```

---

### Task 4: The seventh example — `examples/bounty_board.py` joins every inventory (U1, O-HYG8)

**Files:**
- Move: `sandbox/bounty_board.py` → `examples/bounty_board.py` (`git mv`; content edits limited to the docstring's last paragraph and `ruff format`)
- Delete: `sandbox/test_bounty_board.py` (its content is re-homed below)
- Create: `tests/real_host/test_example_bounty_board_real.py`, `tests/goldens/wasm/bounty_board.wat.txt`
- Modify: `tests/unit/test_emitter_end_to_end.py:102-115` (`EXAMPLE_BOUNTY_BOARD`, `EXAMPLES`) and `:673-682` (`CONSTRUCTOR_BEARING`); `tests/unit/test_examples.py:150-160` (the `{"errors", "allowance_token"}` set) + a new section + the cross-inventory test; `tests/unit/test_emitter_printer.py:380-392` (`FIXTURE_SOURCES`); `tests/unit/test_harness_hostfns.py:1001-1013` (`_FIXTURES`); `tests/unit/test_frontend_fuzz.py:1078-1085` (the exact `examples` list + its docstring sentence); `tests/real_host/test_examples_real.py:38-45` (import the new constant)
- Test: everything above; `SERPENT_REQUIRE_REAL_HOST=1` for the real leg

**Interfaces:**
- Consumes: `load_example(path) -> ModuleType`, `start(path) -> (BuildResult, FullHost, MiniHost)`, `answer(host, mini, name, *words) -> object`, `host.val_word(chain_value) -> int`, `val.pack_u32val(n)`, `val.VOID_VAL`, `host.events: list[tuple[tuple[int, ...], int]]`, `host.chain_value(word)`; `Env(auths=...)`, `deploy(cls, env, *ctor)`, `env.frame()`, `env.advance(n)`, `env.published_events`; `RealEnv(auths=...)`, `deploy_source(path, *ctor)`, `RealContract.invoke/events_for_sequence/storage(bucket).get/ttl/auths`, `RealHostError.underlying`, `RealContractError.code`; `stellar_sdk.strkey.StrKey.encode_contract`.
- Produces: `tests.unit.test_emitter_end_to_end.EXAMPLE_BOUNTY_BOARD: Path`; `tests/unit/test_examples.py::test_every_example_is_in_every_hand_kept_inventory`.

- [ ] **Step 1: Move the contract; fix the docstring pointer; format**

```bash
git mv sandbox/bounty_board.py examples/bounty_board.py
git rm sandbox/test_bounty_board.py
```

In `examples/bounty_board.py`, replace the docstring's last paragraph:

```python
Every method runs unchanged at tier 1 (plain Python, `serpent.env.Env`), on
the embedded real host (`serpent.testing.RealEnv`), and as the deployable
WASM `stellar serpent build examples/bounty_board.py` writes next to this
file. `tests/unit/test_examples.py` runs the first and the mini host and
compares them; `tests/real_host/test_example_bounty_board_real.py` runs the
real host against tier 1 on the same sequences.
```

Run `uv run --no-sync ruff format examples/bounty_board.py` (K10 measured one file would be reformatted — this is that), then `uv run --no-sync mypy --strict examples/bounty_board.py` → clean (K10). Do NOT change any other line of the contract: it is Elliot's (ruling E7).

- [ ] **Step 2: The inventories (each a failing "directory == EXAMPLES" test first)**

Run `uv run --no-sync pytest -q tests/unit/test_examples.py::test_examples_is_a_flat_directory_of_modules` → FAIL (the directory has a file `EXAMPLES` lacks). Then:

1. `tests/unit/test_emitter_end_to_end.py`: add `EXAMPLE_BOUNTY_BOARD = EXAMPLES_DIR / "bounty_board.py"` after `EXAMPLE_SHAPES` and append it to `EXAMPLES`; add `EXAMPLE_BOUNTY_BOARD` to `CONSTRUCTOR_BEARING` (it has `__init__` → floor 22) and extend that frozenset's docstring with one sentence: "M1-G's `bounty_board.py` has an `__init__` (the admin is recorded at deploy) and is listed."
2. `tests/unit/test_examples.py::test_every_example_compiles`: the set becomes `{"errors", "allowance_token", "bounty_board"}`; its docstring's "those two declare 22" becomes "those three".
3. `tests/unit/test_emitter_printer.py` `FIXTURE_SOURCES`: append `("examples/bounty_board.py", "bounty_board")`.
4. `tests/unit/test_harness_hostfns.py` `_FIXTURES`: append `_ROOT / "examples" / "bounty_board.py"`; its comment gains "and M1-G the seventh, `bounty_board.py`".
5. `tests/unit/test_frontend_fuzz.py`: the exact `examples` list gains `"examples/bounty_board.py"` (sorted position: after `allowance_token.py`); the docstring gains "and M1-G Task 4 added `examples/bounty_board.py` (the seventh: every M1 surface in one contract)".
6. `tests/real_host/test_examples_real.py`: add `EXAMPLE_BOUNTY_BOARD` to the import list (the real leg's own tests live in the new module; the import keeps the inventory visible from this file's docstring census).

Generate the golden: `SERPENT_REGEN_GOLDENS=1 uv run --no-sync pytest -q tests/unit/test_emitter_printer.py -k bounty_board` → writes `tests/goldens/wasm/bounty_board.wat.txt`. Read it: `symsmall_cmp` must appear (the `tag() != Symbol("Open")` compares), `obj_cmp` must not be called on two small words.

- [ ] **Step 3: The tier-1 vs mini-host two-leg test**

Append to `tests/unit/test_examples.py` (import `EXAMPLE_BOUNTY_BOARD` alongside the others; `hashlib`, `StrKey` as needed):

```python
# ===========================================================================
# bounty_board: every M1 surface in one contract (M1-G, U1)
# ===========================================================================

import hashlib

from stellar_sdk.strkey import StrKey


def _role(label: str) -> Address:
    """A deterministic CONTRACT strkey per role, the same derivation the real
    leg uses (`tests/real_host/test_example_bounty_board_real.py`): contract
    strkeys because the real host's allow-set mocks CONTRACT authorizers only
    (ruling F-B2); the mini host mocks all auths and does not care."""
    return Address(StrKey.encode_contract(hashlib.sha256(label.encode()).digest()))


def test_the_bounty_board_example_answers_the_same_at_tier_1_and_as_wasm() -> None:
    """post / claim / complete plus every read, on both legs, compared to each
    other before the literal pins.

    Two deliberate omissions on the mini-host leg, both `mini_host_gap`s:
    `priority_of` returns an int enum, which the mini host hands back as a
    bare `U32` while tier 1 answers `Priority.Low` (E9: the two are not equal
    at tier 1; the REAL leg decodes the return through the method's own
    annotation and pins it), so `is_urgent`'s `Bool` stands in here; and the
    TTL lapse of a claim, which the mini host cannot model at all
    (`extend_contract_data_ttl` is a recorded no-op) and the real leg proves.
    """
    module = load_example(EXAMPLE_BOUNTY_BOARD)
    admin, poster, worker = _role("admin"), _role("poster"), _role("worker")
    env = Env(auths=(admin, poster, worker))
    board = deploy(module.BountyBoard, env, admin)
    with env.frame():
        tier_1: list[object] = [
            board.post(env, poster, U32(50), module.Priority.High),
            board.post(env, poster, U32(20), module.Priority.Low),
            board.total_posted(env),
            board.status_of(env, U32(1)),
            board.open_ids(env),
        ]
        board.claim(env, U32(1), worker)
        tier_1 += [board.status_of(env, U32(1)), board.worker_of(env, U32(1)), board.open_ids(env)]
        tier_1 += [board.complete(env, U32(1)), board.status_of(env, U32(1))]
        tier_1 += [board.is_urgent(env, U32(1)), board.reward_of(env, U32(2)), board.posted_at(env, U32(2))]
        tier_1_codes = [
            _tier_1_code(board.post, env, poster, U32(0), module.Priority.Low),
            _tier_1_code(board.complete, env, U32(2)),
            _tier_1_code(board.claim, env, U32(1), worker),
            _tier_1_code(board.worker_of, env, U32(99)),
        ]
        tier_1_topics = [topics[0] for topics, _data in env.published_events]

    _built, host, mini = start(EXAMPLE_BOUNTY_BOARD)
    admin_w, poster_w, worker_w = (host.val_word(a) for a in (admin, poster, worker))
    high, low = val.pack_u32val(2), val.pack_u32val(0)
    one, two = val.pack_u32val(1), val.pack_u32val(2)
    assert mini.invoke("__constructor", admin_w) == val.VOID_VAL
    from_wasm: list[object] = [
        answer(host, mini, "post", poster_w, val.pack_u32val(50), high),
        answer(host, mini, "post", poster_w, val.pack_u32val(20), low),
        answer(host, mini, "total_posted"),
        answer(host, mini, "status_of", one),
        answer(host, mini, "open_ids"),
    ]
    assert mini.invoke("claim", one, worker_w) == val.VOID_VAL
    from_wasm += [
        answer(host, mini, "status_of", one),
        answer(host, mini, "worker_of", one),
        answer(host, mini, "open_ids"),
    ]
    from_wasm += [answer(host, mini, "complete", one), answer(host, mini, "status_of", one)]
    from_wasm += [
        answer(host, mini, "is_urgent", one),
        answer(host, mini, "reward_of", two),
        answer(host, mini, "posted_at", two),
    ]
    wasm_codes = [
        _wasm_code(mini, "post", poster_w, val.pack_u32val(0), low),
        _wasm_code(mini, "complete", two),
        _wasm_code(mini, "claim", one, worker_w),
        _wasm_code(mini, "worker_of", val.pack_u32val(99)),
    ]
    wasm_topics = [host.chain_value(topics[0]) for topics, _data in host.events]

    assert from_wasm == tier_1
    assert wasm_codes == tier_1_codes == [5, 3, 2, 1]
    assert wasm_topics == tier_1_topics
    assert tier_1 == [
        U32(1), U32(2), U32(2), Symbol("Open"), Vec(U32, [U32(1), U32(2)]),
        Symbol("Claimed"), worker, Vec(U32, [U32(2)]),
        U32(50), Symbol("Paid"),
        Bool(True), U32(20), U32(env.ledger().sequence().value),
    ]
    assert tier_1_topics == [Symbol("posted"), Symbol("posted"), Symbol("claimed"), Symbol("completed")]
```

`_wasm_code(mini: engine.MiniHost, name: str, *args: int) -> int` already exists (`tests/unit/test_examples.py:938`): the mini-host twin of `_tier_1_code`, it invokes and returns the contract's `fail_with_error` code out of `engine.HostError`. Reuse it; add nothing. `env.ledger().sequence()` at tier 1 is the default sequence constant; the mini host's `ledger_sequence` is the same shared default (D9's shared home), which is why `posted_at` compares.

Run: `uv run --no-sync pytest -q tests/unit/test_examples.py -k bounty` → pass. If a leg disagrees, STOP and report the row (a disagreement here is a finding, not something to pin around).

- [ ] **Step 4: The real-host leg (Elliot's five tests, typed)**

Create `tests/real_host/test_example_bounty_board_real.py`:

```python
"""The bounty board on the real host, tier 1 as the other leg (M1-G Task 4, U1).

The seventh example is the one contract that touches every M1 authoring
surface, so its sequences are the closest thing the suite has to a user's
own test file. Every test here runs the SAME steps at tier 1 (`serpent.env`)
and on the embedded real host (`serpent.testing.RealEnv`), asserts the decoded
answers, error codes, and events are EQUAL, and only then pins the literals.
Per-test `real_host` marks (M12), never a module-level `pytestmark`.
"""

from __future__ import annotations

import functools
import hashlib
from collections.abc import Callable
from typing import Any, cast

import pytest
from stellar_sdk.strkey import StrKey

from serpent import U32, Address, Bool, Symbol, Vec
from serpent.env import AuthorizationFailed, Env, deploy
from serpent.testing import RealContractError, RealEnv, RealHostError
from tests.unit.test_emitter_end_to_end import EXAMPLE_BOUNTY_BOARD
from tests.unit.test_examples import load_example

board = load_example(EXAMPLE_BOUNTY_BOARD)


def _contract_address(label: str) -> Address:
    """A deterministic CONTRACT strkey per role: the real host mocks
    authorization by registering a stand-in contract at the authorizer's
    address (ruling F-B2; account authorizers need real signatures: M2)."""
    return Address(StrKey.encode_contract(hashlib.sha256(label.encode()).digest()))


ADMIN = _contract_address("admin")
POSTER = _contract_address("poster")
WORKER = _contract_address("worker")
OUTSIDER = _contract_address("outsider")
ALLOWED = (ADMIN, POSTER, WORKER)

Step = tuple[str, tuple[Any, ...]]
Outcome = object  # a chain value, None, or ("error", code)


def _outcome(call: Callable[[], object]) -> Outcome:
    try:
        return call()
    except RealContractError as exc:
        return ("error", exc.code)
    except Exception as exc:  # tier 1: @contracterror members are exception classes
        code = getattr(type(exc), "code", None)
        if code is None:
            raise
        return ("error", code)


def _invoke_tier1(inst: Any, method: str, args: tuple[Any, ...], env: Env) -> object:
    return getattr(inst, method)(env, *args)


def _tier1(steps: list[Step], *, advance_before: dict[int, int] | None = None) -> tuple[list[Outcome], Any]:
    env = Env(auths=ALLOWED)
    inst = deploy(board.BountyBoard, env, ADMIN)
    answers: list[Outcome] = []
    for n, (method, args) in enumerate(steps):
        if advance_before and n in advance_before:
            env.advance(advance_before[n])
        with env.frame():
            answers.append(_outcome(functools.partial(_invoke_tier1, inst, method, args, env)))
    return answers, env.published_events


def _real(steps: list[Step], *, advance_before: dict[int, int] | None = None) -> tuple[list[Outcome], Any]:
    env = RealEnv(auths=ALLOWED)
    contract = env.deploy_source(EXAMPLE_BOUNTY_BOARD, ADMIN)
    answers: list[Outcome] = []
    for n, (method, args) in enumerate(steps):
        if advance_before and n in advance_before:
            env.advance(advance_before[n])
        answers.append(_outcome(functools.partial(contract.invoke, method, *args)))
    return answers, contract.events_for_sequence()


HAPPY_PATH: list[Step] = [
    ("post", (POSTER, U32(50), board.Priority.High)),
    ("post", (POSTER, U32(20), board.Priority.Low)),
    ("total_posted", ()),
    ("status_of", (U32(1),)),
    ("open_ids", ()),
    ("claim", (U32(1), WORKER)),
    ("status_of", (U32(1),)),
    ("worker_of", (U32(1),)),
    ("open_ids", ()),
    ("complete", (U32(1),)),
    ("status_of", (U32(1),)),
    ("priority_of", (U32(2),)),
    ("is_urgent", (U32(1),)),
    ("reward_of", (U32(2),)),
]


@pytest.mark.real_host
def test_the_happy_path_answers_the_same_on_both_legs() -> None:
    tier1, tier1_events = _tier1(HAPPY_PATH)
    real, real_events = _real(HAPPY_PATH)
    assert real == tier1
    assert real_events == tier1_events
    assert tier1 == [
        U32(1), U32(2), U32(2), Symbol("Open"), Vec(U32, [U32(1), U32(2)]),
        None, Symbol("Claimed"), WORKER, Vec(U32, [U32(2)]),
        U32(50), Symbol("Paid"), board.Priority.Low, Bool(True), U32(20),
    ]
    assert [topics[0] for topics, _data in tier1_events] == [
        Symbol("posted"), Symbol("posted"), Symbol("claimed"), Symbol("completed"),
    ]


ERRORS: list[Step] = [
    ("post", (POSTER, U32(0), board.Priority.Low)),  # ZeroReward
    ("post", (POSTER, U32(5), board.Priority.Medium)),  # id 1
    ("complete", (U32(1),)),  # NotClaimed
    ("claim", (U32(1), WORKER)),
    ("claim", (U32(1), WORKER)),  # NotOpen
    ("worker_of", (U32(99),)),  # NoSuchBounty
]


@pytest.mark.real_host
def test_every_error_code_agrees_on_both_legs() -> None:
    tier1, _ = _tier1(ERRORS)
    real, _ = _real(ERRORS)
    assert real == tier1
    assert tier1 == [("error", 5), U32(1), ("error", 3), None, ("error", 2), ("error", 1)]


EXPIRY: list[Step] = [
    ("post", (POSTER, U32(9), board.Priority.Medium)),
    ("claim", (U32(1), WORKER)),
    ("complete", (U32(1),)),
]


@pytest.mark.real_host
def test_a_claim_lapses_after_its_ttl_on_both_legs() -> None:
    """The claim is a temporary entry extended to CLAIM_TTL ledgers; one past
    that, both legs refuse with the contract's own code rather than paying."""
    past = {2: board.CLAIM_TTL.value + 1}
    tier1, _ = _tier1(EXPIRY, advance_before=past)
    real, _ = _real(EXPIRY, advance_before=past)
    assert real == tier1
    assert tier1[2] == ("error", 4)


@pytest.mark.real_host
def test_an_address_outside_the_allow_set_cannot_post() -> None:
    """Authorization is a host TRAP, not a contract error."""
    env = Env(auths=ALLOWED)
    inst = deploy(board.BountyBoard, env, ADMIN)
    with env.frame(), pytest.raises(AuthorizationFailed):
        inst.post(env, OUTSIDER, U32(1), board.Priority.Low)

    real = RealEnv(auths=ALLOWED)
    contract = real.deploy_source(EXAMPLE_BOUNTY_BOARD, ADMIN)
    with pytest.raises(RealHostError) as info:
        contract.invoke("post", OUTSIDER, U32(1), board.Priority.Low)
    assert not isinstance(info.value, RealContractError)
    assert info.value.underlying is not None and info.value.underlying[0] == "Auth"
    assert contract.auths() == ()


@pytest.mark.real_host
def test_storage_reads_back_through_the_real_host_by_type() -> None:
    real = RealEnv(auths=ALLOWED)
    contract = real.deploy_source(EXAMPLE_BOUNTY_BOARD, ADMIN)
    contract.invoke("post", POSTER, U32(7), board.Priority.High)
    contract.invoke("claim", U32(1), WORKER)
    record = cast(Any, contract.storage("persistent").get(board.BountyKey(bounty_id=U32(1)), board.Bounty))
    assert record == board.Bounty(poster=POSTER, reward=U32(7), priority=board.Priority.High, posted_at=record.posted_at)
    assert contract.storage("persistent").get(board.StatusKey(status_of=U32(1)), board.Status) == board.Status.Claimed(WORKER)
    assert contract.storage("temporary").get(board.ClaimKey(claim_on=U32(1)), Address) == WORKER
    assert contract.storage("temporary").ttl(board.ClaimKey(claim_on=U32(1))) == board.CLAIM_TTL.value
```

Run: `SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q tests/real_host/test_example_bounty_board_real.py` → 5 passed. `uv run --no-sync mypy --strict` → clean (the three K10 errors are gone by construction: `functools.partial` and the `cast`).

- [ ] **Step 5: The cross-inventory test (O-HYG8)**

Append to `tests/unit/test_examples.py`:

```python
def test_every_example_is_in_every_hand_kept_inventory() -> None:
    """O-HYG8 (E attn item 7): five inventories beyond `EXAMPLES` are kept BY
    HAND and never failed on an omission -- the printer's `FIXTURE_SOURCES`,
    the mock's `_FIXTURES`, the fuzz `CORPUS`, the goldens directory, and the
    real leg's module. This is the net: every example must be in each."""
    from tests.unit.test_emitter_printer import FIXTURE_SOURCES, GOLDEN_DIR
    from tests.unit.test_frontend_fuzz import CORPUS
    from tests.unit.test_harness_hostfns import _FIXTURES

    stems = {path.stem for path in EXAMPLES}
    assert stems <= {stem for source, stem in FIXTURE_SOURCES if source.startswith("examples/")}
    assert set(EXAMPLES) <= {path.resolve() for path in _FIXTURES}
    assert {f"examples/{path.name}" for path in EXAMPLES} <= {name for name, _ in CORPUS}
    for stem in stems:
        assert (GOLDEN_DIR / f"{stem}.wat.txt").is_file(), f"no golden for {stem}"
    real_leg = (Path(__file__).resolve().parents[1] / "real_host").glob("test_example*_real.py")
    real_text = "".join(p.read_text(encoding="utf-8") for p in real_leg) + (
        Path(__file__).resolve().parents[1] / "real_host" / "test_examples_real.py"
    ).read_text(encoding="utf-8")
    for path in EXAMPLES:
        constant = f"EXAMPLE_{path.stem.upper()}"
        assert constant in real_text, f"{constant} is not used by any real-host example module"
```

`GOLDEN_DIR` must be whatever `test_emitter_printer.py` names its goldens directory (`grep -n "GOLDEN" tests/unit/test_emitter_printer.py`); `CORPUS` is `test_frontend_fuzz.py`'s corpus of `(name, source)` pairs (`:1050-1080`). Prove the test has teeth by temporarily removing the bounty board from `FIXTURE_SOURCES` → it fails naming it; restore.

- [ ] **Step 6: Gates, commit**

Four gates. Expected suite: baseline + the new tests (≈ +12 unit, +5 real). The real-host count grows; note the new total in the ledger for Task 9's CI assertion.

```bash
git add examples/bounty_board.py sandbox/ tests/unit/test_emitter_end_to_end.py tests/unit/test_examples.py tests/unit/test_emitter_printer.py tests/unit/test_harness_hostfns.py tests/unit/test_frontend_fuzz.py tests/real_host/test_examples_real.py tests/real_host/test_example_bounty_board_real.py tests/goldens/wasm/bounty_board.wat.txt
git commit -m "feat(examples): promote the bounty board to the seventh example

One contract that touches every M1 authoring surface: a constructor,
struct keys, a tagged union with three payload shapes, an int enum, an
error enum, three event data formats, temporary storage with a TTL, and
require_auth on the poster, the worker, and the stored admin. It joins
every example inventory, gets a disassembly golden, runs tier 1 against
the mini host and against the real host, and a new cross-inventory test
fails on the next example that misses one of the hand-kept lists."
```

---

### Task 5: Frontend hygiene — a parameter shadowing a declared name is SPT2004; the bridge-needle gate derives its raise sites (O-HYG5, O-HYG4)

**Files:**
- Modify: `src/serpent/compiler/frontend.py:441-443`; `tests/unit/test_bridging_completeness.py:500-577`; `docs/subset.md` (regenerated)
- Create: `tests/must_reject/names/param_shadows_declared_type.py`, `tests/must_reject/names/param_shadows_module_constant.py`
- Test: `tests/unit/test_must_reject.py` (the runner picks fixtures up automatically), `tests/unit/test_frontend.py` (a located-diagnostic pin), `tests/unit/test_bridging_completeness.py`

**Interfaces:**
- Consumes: `Diagnostics.error(code, loc, message, *, help=None, notes=())`; `_INTENT` (frontend.py:141); `serpent.compiler.ctx._SHADOW_HELP`; `FuncSig.params: list[tuple[str, Ty, Loc]]`; `frontend._reserved_names(loaded) -> dict[str, str]` (values: "an imported name", "a module constant", "a module-level helper", "a declared type", "the contract class").
- Produces: SPT2004 at the PARAMETER's `Loc` when its name is in `module_reserved`, message `f"{_INTENT['SPT2004']}"`, note `f"parameter \`{name}\` already names {kind}"`, help `_SHADOW_HELP`.

- [ ] **Step 1: The fixtures (failing first)**

`tests/must_reject/names/param_shadows_declared_type.py`:

```python
# serpent:reject SPT2004
# serpent:at HERE
# serpent:message name shadows an existing declaration
# serpent:doc-title parameter shadows a declared type
from serpent import Env, U32, contract, contracttype


@contracttype
class Point:
    x: U32


@contract
class Contract:
    def compute(self, env: Env, Point: U32) -> U32:  # HERE
        return Point
```

`tests/must_reject/names/param_shadows_module_constant.py`:

```python
# serpent:reject SPT2004
# serpent:at HERE
# serpent:message name shadows an existing declaration
# serpent:doc-title parameter shadows a module constant
from serpent import Env, U32, Symbol, contract

LIMIT = U32(10)


@contract
class Contract:
    def compute(self, env: Env, LIMIT: U32) -> U32:  # HERE
        return LIMIT
```

Run: `uv run --no-sync pytest -q tests/unit/test_must_reject.py` → the two new fixtures FAIL (today the module compiles, or rejects elsewhere). Read the failure: it must be "compiled with no diagnostic" (the silent shadow), not a different code — if a different code fires first, record which and stop for a ruling.

- [ ] **Step 2: The check**

In `src/serpent/compiler/frontend.py`, replace lines 441-443:

```python
    reserved = dict(module_reserved)
    for name, _ty, loc in sig.params:
        taken = module_reserved.get(name)
        if taken is not None:
            # A parameter that shadows a module-level reservation used to WIN
            # silently (the overwrite below), so `def f(self, env, Point: U32)`
            # in a module declaring `class Point` compiled, and every `Point`
            # in the body meant the parameter. SPT2004 already names this
            # shape ("Local/param shadows ... type name"); this is the check
            # the registry row promised (M1-G Task 5, O-HYG5).
            sink.error(
                "SPT2004",
                loc,
                _INTENT["SPT2004"],
                help=_SHADOW_HELP,
                notes=(f"parameter `{name}` already names {taken}",),
            )
        reserved[name] = "a parameter"
```

Add `from serpent.compiler.ctx import _SHADOW_HELP` to the ctx import line (the in-package private import has precedent: `_class_doc`). Confirm `_INTENT["SPT2004"]` reads "name shadows an existing declaration" (the fixtures' `serpent:message`).

Run the two fixtures → pass. Run the WHOLE suite: any example, fixture, or fuzz shape that used a parameter named like a module-level name now rejects — each such failure is a real finding; fix the contract (rename the parameter) if it is in `tests/fixtures/` or `examples/`, and report every rename in the task report. If `examples/` needs a rename, that is an authoring-surface change to a shipped example — report BLOCKED for a controller decision rather than renaming silently.

- [ ] **Step 3: A located pin in `tests/unit/test_frontend.py`**

```python
def test_a_parameter_named_like_a_declared_type_is_spt2004_at_the_parameter() -> None:
    source = (
        "from serpent import Env, U32, contract, contracttype\n\n\n"
        "@contracttype\nclass Point:\n    x: U32\n\n\n"
        "@contract\nclass C:\n    def f(self, env: Env, Point: U32) -> U32:\n        return Point\n"
    )
    with pytest.raises(CompileError) as info:
        compile_module(source, "shadow.py")
    (diag,) = [d for d in info.value.diagnostics if d.code == "SPT2004"]
    assert diag.loc.line == 11
    assert "parameter `Point` already names a declared type" in diag.notes
```

Adjust the line number to where the `def` sits in the string (count the `\n`s); adjust `diag.notes`'s type if it is a tuple of strings (`in` works either way).

- [ ] **Step 4: Regenerate the subset doc**

`uv run --no-sync python -m serpent.compiler._render_docs`; `git diff --stat docs/subset.md` shows only the two new SPT2004 entries. The drift test (`grep -n "subset" tests/unit/test_must_reject.py tests/unit/test_diagnostics.py` names it) passes.

- [ ] **Step 5: The bridge gate derives its sites (failing first)**

In `tests/unit/test_bridging_completeness.py`, replace `_BRIDGED_RAISE_SOURCES` (lines 508-527) and the body of `test_every_declaration_layer_raise_carries_a_bridge_needle` with:

```python
#: The declaration-layer MODULES (not functions): every `raise` anywhere in
#: them is either bridged (its message carries a `loader._BRIDGE_RULES`
#: needle) or listed below with a reason. M1-E2's version of this gate named
#: FUNCTIONS, so a raise added to any other function was invisible
#: (one-directional blind spot, E2 attn §3; O-HYG4/D11) -- deriving the walk
#: from the module closes it.
_DECLARATION_LAYER_MODULES: tuple[str, ...] = ("serpent.decorators", "serpent.types._udt")

#: `(module, message fragment)` for raises a USER cannot reach through a
#: declaration -- internal invariants, or paths the loader intercepts before
#: the decorator runs -- each with its reason. Keyed on TEXT (the M1-F
#: allowlist lesson), so a reworded message re-enters the gate.
_UNBRIDGED_BY_DESIGN: frozenset[tuple[str, str]] = frozenset(
    {
        # populated in Step 6 from the first run's list, one entry per raise,
        # each with a one-line reason comment
    }
)


def test_every_declaration_layer_raise_carries_a_bridge_needle() -> None:
    needles = [rule.needle for rule in loader._BRIDGE_RULES if rule.needle]
    unbridged: list[tuple[str, int, str]] = []
    for module_name in _DECLARATION_LAYER_MODULES:
        module = importlib.import_module(module_name)
        tree = ast.parse(pathlib.Path(module.__file__ or "").read_text(encoding="utf-8"))
        for raised in (n for n in ast.walk(tree) if isinstance(n, ast.Raise)):
            chunks = _raise_message_chunks(raised)
            if any(needle in chunk for chunk in chunks for needle in needles):
                continue
            if any(fragment in chunk for chunk in chunks for m, fragment in _UNBRIDGED_BY_DESIGN if m == module_name):
                continue
            unbridged.append((module_name, raised.lineno, " | ".join(chunks) or "<no literal message>"))
    assert not unbridged, (
        "declaration-site raise(s) with neither a `loader._BRIDGE_RULES` needle nor an "
        f"`_UNBRIDGED_BY_DESIGN` entry -- each would fall to SPT1037: {unbridged}"
    )


def test_the_by_design_list_is_live() -> None:
    """Every allowlisted fragment still matches a raise in its module -- a stale
    entry is deleted, not kept."""
    for module_name, fragment in _UNBRIDGED_BY_DESIGN:
        module = importlib.import_module(module_name)
        source = pathlib.Path(module.__file__ or "").read_text(encoding="utf-8")
        assert fragment in source, (module_name, fragment)
```

Run → FAIL listing every raise outside the old eight+four functions.

- [ ] **Step 6: Classify each listed raise**

For each `(module, line, message)` in the failure: read the raise. If a user's declaration can reach it (a decorator argument shape, a field annotation, a case declaration), it needs a needle: add a `_BridgeRule` in `loader.py` mapping a distinctive fragment of its message to the EXISTING code whose intent fits (`SPT4xxx` shape codes, `SPT5xxx` limits, `SPT2004` shadowing) — if NO existing code's intent honestly fits, return BLOCKED naming the raise (registry additions are the controller's). If it is an internal invariant (an `AssertionError`, a "should be unreachable", a re-raise of a TypeError the loader already intercepted), add `(module, fragment)` to `_UNBRIDGED_BY_DESIGN` with a reason comment. Re-run until green; then run the whole suite (new bridge rules change which code a shape gets — `test_must_reject` and `test_bridging_completeness`'s `_ROWS` must agree; add a `_ROWS` row per new rule).

- [ ] **Step 7: Gates, commit**

```bash
git add src/serpent/compiler/frontend.py src/serpent/compiler/loader.py tests/must_reject/names/ tests/unit/test_frontend.py tests/unit/test_bridging_completeness.py docs/subset.md
git commit -m "fix(frontend): reject a parameter that shadows a module-level name

A parameter named like a declared type, module constant, helper, or
import used to overwrite the reservation silently, so every use in the
body meant the parameter. SPT2004 already describes the shape; the check
now fires at the parameter with a note naming what it shadows, and two
fixtures pin it. The bridge-needle gate now walks every raise in the
declaration-layer modules instead of a hand-kept list of functions."
```

---

### Task 6: The mock refuses what the host refuses — `FullHost(strict_obj_cmp=True)` (O-MOCK1, ruling E8)

**Files:**
- Modify: `tests/harness/hostfns.py:170-260` (`FullHost.__init__`, `obj_cmp`); `tests/unit/test_emitter_symbol_compare.py:100-235` (retire `StrictObjCmpHost`, keep `RoutingProbeHost` over `FullHost`)
- Create: `tests/unit/test_harness_strict_obj_cmp.py`
- Test: the new module + the whole suite (every tier-2a test now runs strict)

**Interfaces:**
- Consumes: `val.is_object(word)`, `tests.harness.errors.HostTrap`, `engine.MiniHost(wasm, imports=...)`, `host.attach(mini)`, `val.pack_u32val`.
- Produces: `FullHost.__init__(self, *, strict_obj_cmp: bool = True)`; `FullHost.strict_obj_cmp: bool`; `obj_cmp` raising `HostTrap` with a message containing `"two non-object args to obj_cmp"` when strict and both words are non-objects.

- [ ] **Step 1: The regression test on the DEPLOYED shapes bytes (failing first)**

Create `tests/unit/test_harness_strict_obj_cmp.py`:

```python
"""The mock's `obj_cmp` refuses two non-object words, as the host does (M1-G Task 6, ruling E8).

Until M1-G the mini host accepted `obj_cmp` on two small `Val`s and answered
from tier-1 `val_cmp`, so the shipped small-Symbol compare bug (M1-F B1) was
green at tier 2a and found only by the real host. The deployed shapes bytes
(`tests/real_host/fixtures/testnet/shapes/deployed.wasm`) still carry that
lowering, trap on chain and on the embedded host, and are therefore the
regression fixture: under the default `FullHost` they must trap here too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from serpent import val
from tests.harness import engine
from tests.harness.errors import HostTrap
from tests.harness.hostfns import FullHost

DEPLOYED_SHAPES = (
    Path(__file__).resolve().parents[1] / "real_host" / "fixtures" / "testnet" / "shapes" / "deployed.wasm"
)


def _drawing(host: FullHost) -> engine.MiniHost:
    mini = engine.MiniHost(DEPLOYED_SHAPES.read_bytes(), imports=host.bindings())
    host.attach(mini)
    assert mini.invoke("draw_rect", val.pack_u32val(5), val.pack_u32val(2)) == val.VOID_VAL
    return mini


def test_the_deployed_shapes_area_traps_under_the_default_mock() -> None:
    host = FullHost()
    assert host.strict_obj_cmp is True
    mini = _drawing(host)
    with pytest.raises(HostTrap, match="two non-object args to obj_cmp"):
        mini.invoke("area")


def test_the_lax_mock_still_answers_for_archaeology() -> None:
    host = FullHost(strict_obj_cmp=False)
    mini = _drawing(host)
    word = mini.invoke("area")
    assert word is not None
    assert host.chain_value(word) == U32(10)
```

(`from serpent import U32` joins the imports.) Also add:

```python
def test_two_object_words_still_compare() -> None:
    host = FullHost()
    from serpent import Symbol

    left = host.val_word(Symbol("a_long_symbol_name"))
    right = host.val_word(Symbol("b_long_symbol_name"))
    assert val.is_object(left) and val.is_object(right)
    assert host.obj_cmp(left, right) == val.as_u64(-1)


def test_mixed_small_and_object_still_compares() -> None:
    host = FullHost()
    from serpent import Symbol

    small = host.val_word(Symbol("abc"))
    obj = host.val_word(Symbol("a_long_symbol_name"))
    assert not val.is_object(small) and val.is_object(obj)
    host.obj_cmp(small, obj)  # answers, does not raise
```

Run → FAIL (`__init__` takes no `strict_obj_cmp`; `area` answers 10).

- [ ] **Step 2: The flag**

In `tests/harness/hostfns.py`, `FullHost.__init__` becomes `def __init__(self, *, strict_obj_cmp: bool = True) -> None:` and records `self.strict_obj_cmp = strict_obj_cmp` with the docstring line: "`strict_obj_cmp` (default True, M1-G ruling E8): `obj_cmp` refuses two non-object words with `HostTrap`, as the real host does (`Error(Value, UnexpectedType)`, 'two non-object args to obj_cmp'); `False` reproduces the pre-M1-G lax model for archaeology only." `obj_cmp` becomes:

```python
    def obj_cmp(self, left: int, right: int) -> int:
        self._log("obj_cmp", left, right)
        if self.strict_obj_cmp and not val.is_object(left) and not val.is_object(right):
            raise HostTrap(
                "two non-object args to obj_cmp: the real host answers Error(Value, "
                f"UnexpectedType) for ({left:#x}, {right:#x}); the emitter must compare "
                "small words in the guest (M1-F Task 0's guard)"
            )
        return val.as_u64(self.compare(left, right))
```

Update the module docstring's "`obj_cmp` delegates; it does not decide" paragraph (hostfns.py:34-50) with one sentence: the ONE thing it decides is the refusal above. `HostTrap` is already importable in `hostfns.py` (check the import; add `from tests.harness.errors import HostTrap` if not).

- [ ] **Step 3: Retire the subclass**

In `tests/unit/test_emitter_symbol_compare.py`: delete `StrictObjCmpHost`; `RoutingProbeHost(FullHost)`; `_strict` builds `FullHost()`; retype `_call`'s `host` parameter and the docstring at lines 104-108 ("That host is defined HERE ... making it strict would change what every other tier-2a test is asserting") becomes: "The mock is strict by default since M1-G (ruling E8), so the behavioural pins run under plain `FullHost`; `RoutingProbeHost` only adds the Void answer." Run the WHOLE suite: any other tier-2a test that relied on lax `obj_cmp` for two small words now traps — that would be a real emitter finding (the guard should make it unreachable); report rather than loosen.

- [ ] **Step 4: Gates, commit**

```bash
git add tests/harness/hostfns.py tests/unit/test_harness_strict_obj_cmp.py tests/unit/test_emitter_symbol_compare.py
git commit -m "test(harness): make the mock refuse obj_cmp on two non-object words

The real host traps on obj_cmp over two small Vals; the mini host used
to answer from tier-1 val_cmp, which is how the shipped small-Symbol
compare bug stayed green at tier 2a. FullHost is now strict by default,
the deployed shapes bytes trap on area under the mock exactly as they
do on chain, and the test-local strict subclass is retired."
```

---

### Task 7: The sanctioned registry wording pass and the shapes docstring (O-HYG1, O-HYG2, O-HYG3, O-HYG6, O-HYG7)

**Files:**
- Modify (SANCTIONED, enumerated below, nothing else): `src/serpent/compiler/codes.py`; `src/serpent/compiler/diagnostics.py` (the SPT1xxx subset note); `src/serpent/spec/sections.py` + `src/serpent/compiler/frontend.py:131` (`_class_doc` → public `class_doc`); `src/serpent/compiler/frontend.py:86-93` (`_host._protocol` → `serpent._host`); `examples/shapes.py:224-228`; `tests/must_reject/shape/struct_field_non_chain_type.py` + `tests/must_reject/types/union_option_payload.py` headers; `tests/unit/test_diagnostics.py` (the `owning_task == "Task 8"` pin and any intent pins); `docs/subset.md`; `tests/goldens/wasm/shapes.wat.txt`
- Test: the whole suite (snapshot pins, the subset drift test, the goldens)

**The sanction (ruling 2026-09-10 "Also ruled")** — text-only, no new code, no renumbering, no meaning reversal:

1. **SPT4012** (O-HYG1): construct `"@contracttype -- non-chain field annotation"` → `"@contracttype/@contractevent field or @contractunion variant payload -- non-chain annotation"`; intent `"struct fields need a chain-type annotation"` → `"fields and variant payloads need a chain-type annotation"`. Both fixtures' `# serpent:message` lines change to the new intent; the loader `_HELP["SPT4012"]` text is re-read for consistency (widen, do not narrow).
2. **Origin fields** (O-HYG2): every `owning_task` without a sub-plan prefix gains `"M1-C "` (`"Task 5"` → `"M1-C Task 5"`, `"Task 4/6"` → `"M1-C Task 4/6"`, …); the prefixed ones (`"M1-D Task 10"`, `"M1-E2 Task 2"`) are already in the target form. `tests/unit/test_diagnostics.py`'s `owning_task == "Task 8"` pin becomes `"M1-C Task 8"`.
3. **Limit numbers out of intents** (O-HYG7 / M1-C final minor 2): `SPT5004` → `"docstring is too long"`, `SPT5005` → `"an exported method has too many parameters"`, `SPT5006` → `"a variant payload carries too many values"`, `SPT8001` → `"the compiled module exceeds the network's contract size limit"`, `SPT8002` → `"the literal pool overflows into the scratch region"`, `SPT8003` → `"the scratch region exceeds the module's single memory page"`, `SPT1020` → `"range() supports only range(stop) and range(start, stop)"` (drop "in M1"). For each, the EMITTING site's message must still carry the number (`limits.py` for SPT5004/5005, `_udt.py`/loader for SPT5006, the emitter's `BuildLimitError` text for SPT8xxx — verify each message already states the limit; if one does not, add the number to the site's message, not the intent). Fixture `# serpent:message` headers for SPT5004/5005/5006 and SPT1020 change to the new intents.
4. **SPT3020 construct list**: append `"a variant call with the wrong arity (\`Circle(1, 2)\` on a one-payload variant), positional struct arguments"` to the construct field (the two honest uses ruled in M1-E2 plan review M2 and M1-C 11b). **SPT3014**: census `grep -rn SPT3014 src/serpent/compiler` and append any emitting shape the construct field omits.
5. **SPT1xxx help cites the subset doc** (O-HYG7): in `diagnostics.py`'s `Diagnostics.error`, when `code.startswith("SPT1")` and no note already mentions `docs/subset.md`, append the note `f"the supported subset is documented at docs/subset.md#{code.lower()}"`. One unit test in `test_diagnostics.py` (an SPT1 code gains the note; an SPT3 code does not; a pre-existing subset note is not duplicated). Check `docs/subset.md`'s anchors: its headings are `#### SPT1001`, so the anchor is `#spt1001`.
6. **`_class_doc` promotion** (M1-C attn §9): rename `spec.sections._class_doc` → `class_doc` and `_own_doc` → `own_doc`, add both to `serpent.spec.__all__`, update `frontend.py:131`'s import.
7. **`frontend.py:86-93`**: import `BASE_PROTOCOL`, `CONSTRUCTOR_MIN_PROTOCOL`, `DEFAULT_TARGET_PROTOCOL`, `ProtocolGateError`, `check_protocol_target`, `compute_protocol_floor`, `declared_protocol` from `serpent._host` (all re-exported there, C11) instead of `serpent._host._protocol`.
8. **`_HELP` order** (O-HYG3): assert with a five-line test in `tests/unit/test_loader.py` that `list(loader._HELP)` is sorted; fix the order if it is not (C9 says it already is).
9. **`examples/shapes.py:224-228`** (O-HYG6): `is_pinned`'s docstring describes the METHOD: "Whether the current shape is in the pinned set: a union used as a storage KEY, looked up by value. `pin()` writes the current shape; this reads it back under a freshly built equal key." Regenerate `tests/goldens/wasm/shapes.wat.txt` (docstrings are in `contractspecv0`, so the bytes move) in the same commit.

- [ ] **Step 1: Snapshot what will move (before editing)**

`uv run --no-sync pytest -q tests/unit/test_diagnostics.py tests/unit/test_must_reject.py tests/unit/test_emitter_printer.py -k "shapes or snapshot or intent"` → all green; record the count.

- [ ] **Step 2: Items 1–4 (codes.py + fixture headers + the diagnostics pin)**

Edit exactly the strings listed. Run `tests/unit/test_must_reject.py` → failures name every fixture whose `serpent:message` no longer matches; update those headers to the new intents (and ONLY those). Run `tests/unit/test_diagnostics.py` → update the `"Task 8"` pin. Regenerate `docs/subset.md`.

- [ ] **Step 3: Item 5 (the subset note) with its test**

In `tests/unit/test_diagnostics.py`:

```python
def test_spt1xxx_diagnostics_cite_the_subset_doc() -> None:
    sink = Diagnostics()
    sink.error("SPT1001", Loc.whole_file("x.py"), "nested functions are not supported")
    sink.error("SPT3018", Loc.whole_file("x.py"), "type mismatch")
    one, three = sink.diagnostics
    assert any("docs/subset.md#spt1001" in note for note in one.notes)
    assert not any("subset.md" in note for note in three.notes)


def test_an_existing_subset_note_is_not_duplicated() -> None:
    sink = Diagnostics()
    sink.error("SPT1005", Loc.whole_file("x.py"), "m", notes=("the supported subset is documented at docs/subset.md#comprehensions",))
    (diag,) = sink.diagnostics
    assert sum("subset.md" in n for n in diag.notes) == 1
```

Implement in `Diagnostics.error` (diagnostics.py:162+): after building `notes`, `if code.startswith("SPT1") and not any("docs/subset.md" in n for n in notes): notes = (*notes, f"the supported subset is documented at docs/subset.md#{code.lower()}")`. Run `tests/unit/test_diagnostics.py` and `tests/unit/test_must_reject.py` (the runner matches `message`, not notes, so fixtures are unaffected; any RENDER golden of an SPT1xxx diagnostic moves — regenerate it and list it in the report).

- [ ] **Step 4: Items 6–9**

Make the renames (grep for every `_class_doc`/`_own_doc` use across `src/` and `tests/`), the import repoint, the `_HELP` order test, the `is_pinned` docstring; regenerate the shapes golden with `SERPENT_REGEN_GOLDENS=1 uv run --no-sync pytest -q tests/unit/test_emitter_printer.py -k shapes`. `git diff tests/goldens/wasm/shapes.wat.txt` must show ONLY data-section/spec bytes moving (the docstring), no code-section change.

- [ ] **Step 5: Gates, commit (one commit; the sanction is one edit)**

```bash
git add src/serpent/compiler/codes.py src/serpent/compiler/diagnostics.py src/serpent/compiler/frontend.py src/serpent/compiler/loader.py src/serpent/spec/sections.py src/serpent/spec/__init__.py examples/shapes.py tests/must_reject tests/unit/test_diagnostics.py tests/unit/test_loader.py docs/subset.md tests/goldens/wasm/shapes.wat.txt
git commit -m "refactor(compiler): apply the sanctioned registry wording pass

Text-only edits under the append-only discipline: SPT4012 names every
position it is emitted for, origin fields carry their sub-plan, limit
numbers move from intents to the emitting messages, SPT3020 lists its
two later honest uses, and every SPT1xxx diagnostic now cites its
docs/subset.md anchor. Also promotes the spec doc readers to public
names, repoints the frontend at the serpent._host re-exports, and
rewrites the shapes example's is_pinned docstring (golden regenerated)."
```

---

### Task 8: The docs site — mkdocs-material, every page generated from the code it describes (O-DOC1, O-DOC2, ruling E5)

**Files:**
- Create: `mkdocs.yml`, `docs/index.md`, `docs/getting-started.md`, `docs/cli.md`, `docs/api.md`, `docs/examples/index.md`, `docs/examples/{counter,errors,structs,events,allowance_token,shapes,bounty_board}.md`, `docs/deployments.md`, `tests/unit/test_docs_site.py`
- Modify: `pyproject.toml` (`[dependency-groups] docs`), `uv.lock`
- Test: `tests/unit/test_docs_site.py`; `uv run --no-sync mkdocs build --strict`

**Interfaces:**
- Consumes: `tests.unit.test_emitter_end_to_end.EXAMPLES`; `tests/goldens/cli/*.help.txt` (Tasks 1-3); `docs/subset.md`, `docs/testing.md` (exist).
- Produces: a `docs` dependency group; `mkdocs.yml` with `strict: true`, `exclude_docs` covering `superpowers/` and `gen_subset.py`, `pymdownx.snippets` with `base_path: ["."]`; the page set above; `docs/deployments.md` with two rows Task 11 extends.

- [ ] **Step 1: The dependency group**

In `pyproject.toml` `[dependency-groups]`, add after `dev`:

```toml
# The docs site (M1-G Task 8, ruling E5). A separate group so `uv sync
# --all-groups` brings it and a plain `uv sync` does not; CI's docs job and
# `tests/unit/test_docs_site.py` both build it with `mkdocs build --strict`.
docs = [
    "mkdocs-material>=9.7,<10",
    "mkdocstrings-python>=2,<3",
]
```

Run `uv sync --all-groups --inexact` (keeps `serpent_host`; re-check `import serpent_host`). `uv run --no-sync mkdocs --version` prints 1.6.x.

- [ ] **Step 2: The drift tests (failing first)**

Create `tests/unit/test_docs_site.py`:

```python
"""The docs site is generated FROM the code (ruling E5; dossier F.1.3): these
tests keep the page set and the source it renders in step with `examples/`
and the CLI goldens, and build the site strictly when mkdocs is installed."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.unit.test_emitter_end_to_end import EXAMPLES

_ROOT = Path(__file__).resolve().parents[2]
_DOCS = _ROOT / "docs"
_CONFIG = _ROOT / "mkdocs.yml"


def _config() -> dict[str, object]:
    return dict(yaml.safe_load(_CONFIG.read_text(encoding="utf-8")))


def test_the_config_is_strict_and_excludes_the_planning_record() -> None:
    config = _config()
    assert config["strict"] is True
    excluded = str(config["exclude_docs"])
    assert "superpowers/" in excluded and "gen_subset.py" in excluded


def test_every_example_has_a_page_rendering_its_source() -> None:
    for path in EXAMPLES:
        page = _DOCS / "examples" / f"{path.stem}.md"
        assert page.is_file(), f"no docs page for {path.name}"
        text = page.read_text(encoding="utf-8")
        assert f'--8<-- "examples/{path.name}"' in text, f"{page.name} does not render {path.name}"


def test_every_example_page_is_in_the_nav_and_nothing_else_is() -> None:
    config = _config()
    nav = config["nav"]
    assert isinstance(nav, list)
    examples_section = next(item["Examples"] for item in nav if isinstance(item, dict) and "Examples" in item)
    listed = {list(entry.values())[0] if isinstance(entry, dict) else entry for entry in examples_section}
    listed.discard("examples/index.md")
    assert listed == {f"examples/{p.stem}.md" for p in EXAMPLES}


def test_the_cli_reference_renders_every_help_golden() -> None:
    text = (_DOCS / "cli.md").read_text(encoding="utf-8")
    for name in ("root", "build", "inspect", "doctor"):
        assert f'--8<-- "tests/goldens/cli/{name}.help.txt"' in text


@pytest.mark.skipif(shutil.which("mkdocs") is None and not _mkdocs_importable(), reason="mkdocs is not installed (the docs group); CI's docs job builds the site for real")
def test_the_site_builds_strictly(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "--site-dir", str(tmp_path / "site")],
        cwd=_ROOT, capture_output=True, text=True, check=False,
    )
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "site" / "index.html").is_file()
    assert (tmp_path / "site" / "examples" / "bounty_board" / "index.html").is_file()


def _mkdocs_importable() -> bool:
    try:
        import mkdocs  # noqa: F401
    except ImportError:
        return False
    return True
```

Move `_mkdocs_importable` above its use in the decorator. `yaml` is pyyaml, which mkdocs depends on; it is present with `--all-groups` (the CI `test` job syncs all groups). Run → FAIL (`mkdocs.yml` missing).

- [ ] **Step 3: `mkdocs.yml`**

```yaml
# serpent's docs site (M1-G Task 8, ruling E5). Every page that describes code
# renders that code from its source: examples via pymdownx.snippets from
# `examples/*.py`, the CLI reference from `tests/goldens/cli/*.help.txt`, the
# subset page from the generated `docs/subset.md`, the API reference from the
# package's own docstrings. `strict: true` fails the build on a broken link.
# `docs/superpowers/` is the PLANNING record, never a page.
site_name: serpent
site_description: Write Soroban smart contracts in Python (experimental)
site_url: https://elliotfriend.github.io/stellar-serpent-sdk/
repo_url: https://github.com/ElliotFriend/stellar-serpent-sdk
repo_name: ElliotFriend/stellar-serpent-sdk
docs_dir: docs
strict: true
exclude_docs: |
  superpowers/
  gen_subset.py
  __pycache__/

theme:
  name: material
  palette:
    - scheme: default
      primary: teal
      toggle:
        icon: material/brightness-7
        name: dark mode
    - scheme: slate
      primary: teal
      toggle:
        icon: material/brightness-4
        name: light mode
  features:
    - navigation.sections
    - navigation.footer
    - content.code.copy
    - toc.follow

plugins:
  - search
  - mkdocstrings:
      handlers:
        python:
          options:
            show_source: false
            show_root_heading: true
            members_order: source
            docstring_style: sphinx

markdown_extensions:
  - admonition
  - tables
  - toc:
      permalink: true
  - pymdownx.superfences
  - pymdownx.snippets:
      base_path: ["."]
      check_paths: true

nav:
  - Home: index.md
  - Getting started: getting-started.md
  - The subset: subset.md
  - Examples:
      - examples/index.md
      - Counter: examples/counter.md
      - Errors: examples/errors.md
      - Structs: examples/structs.md
      - Events: examples/events.md
      - Allowance token: examples/allowance_token.md
      - Shapes: examples/shapes.md
      - Bounty board: examples/bounty_board.md
  - Testing: testing.md
  - CLI reference: cli.md
  - API reference: api.md
  - Deployments: deployments.md
```

`docstring_style: sphinx` is closest to the repo's freeform prose (no Google sections); if mkdocstrings warns under `--strict` about docstring parsing, switch to `docstring_style: google` with `docstring_options: {ignore_init_summary: true}` — whichever produces zero warnings.

- [ ] **Step 4: The pages**

`docs/index.md`:

```markdown
# serpent

Write Soroban smart contracts in Python. **Experimental**: M1 is the
compiler-and-host-interface milestone; there is no PyPI release and no stable
API. Read [Getting started](getting-started.md) to build your first contract,
[The subset](subset.md) for exactly what compiles, and [Testing](testing.md)
before trusting a green run.

## What it is

- A **restricted-Python-subset → WASM compiler**, all Python: `@contract`
  classes with typed methods compile to a deployable Soroban module whose
  `contractspecv0` the stock `stellar` CLI renders and invokes unmodified.
- **Chain types that behave chain-exactly in plain Python** (`U32`, `I128`,
  `Symbol`, `Address`, `Vec`, `Map`, structs, tagged unions, int enums), so a
  contract's methods run as ordinary Python against an in-memory `Env` model
  with no build in the loop.
- **Four testing tiers**, from that model up to recorded testnet simulations,
  with the embedded real `soroban-env-host` as the gate.
- **A Stellar CLI plugin**: `stellar serpent build | inspect | doctor`.

## Honest boundary

Tier 1 and the mini host are models this repository wrote, not the chain.
Only the real host (`serpent.testing.RealEnv`) and recorded testnet fixtures
are evidence about the network. Named gaps -- frame rollback, minimum TTL
floors, container ordering, account authorizers, archival -- are listed in
[Testing](testing.md) and carried to M2.

## Where things are

| | |
|---|---|
| The seven examples | [Examples](examples/index.md), rendered from `examples/` |
| What compiles and what rejects | [The subset](subset.md), generated from the executable `must_reject/` spec |
| The CLI | [CLI reference](cli.md) |
| The public names | [API reference](api.md) |
| Contracts on testnet | [Deployments](deployments.md) |
| Design, decisions, plans | `docs/superpowers/` in the repository |
```

`docs/getting-started.md`:

```markdown
# Getting started

## Install the plugin

serpent is not on PyPI yet. Install the console script straight from the
repository, with the `spec` extra that `build` and `inspect` need:

```sh
uv tool install "serpent[spec] @ git+https://github.com/ElliotFriend/stellar-serpent-sdk"
stellar-serpent doctor
```

`doctor` reports one row per tool. The Stellar CLI finds the plugin as
`stellar-serpent` on PATH, so once `doctor`'s "plugin on PATH" row is `ok`,
every command below also works as `stellar serpent ...`.

Optional but recommended: `wasm-tools` (the version `doctor` names) gives
every build a second, independent validation.

## Write a contract

```python
--8<-- "examples/counter.py"
```

Every method takes `self` then `env: Env`; every parameter and return is a
chain type; errors are `@contracterror` members raised as exceptions; state
lives in `env.storage()`. What is and is not allowed is spelled out, with a
counter-example per rule, in [The subset](subset.md).

## Run it without building

```python
from serpent import U32
from serpent.env import Env, deploy
from counter import Counter, Error

env = Env()
counter = deploy(Counter, env)
with env.frame():
    assert counter.increment(env, U32(5)) == U32(5)
```

That is tier 1: a model, fast, and not the chain. See [Testing](testing.md)
for the real host.

## Build

```sh
stellar serpent build counter.py
```

writes `counter.wasm` beside the source and prints its size, its sha256 (the
on-chain wasm hash), and the protocol it declares. `stellar serpent inspect
counter.wasm` reads it back.

## Deploy and invoke with the stock CLI

serpent rebuilds nothing the Stellar CLI already does:

```sh
stellar contract deploy --wasm counter.wasm --source alice --network testnet
stellar contract invoke --id C... --source alice --network testnet -- increment --step 5
stellar contract info interface --id C... --network testnet
```

A contract with an `__init__` takes its constructor arguments after the `--`
on `deploy`, e.g. `-- --admin G...`.
```

`docs/examples/index.md`: one paragraph and a table (name, what it shows, floor 20/22) for the seven; each `docs/examples/<stem>.md` follows this exact shape (title, one-paragraph "what it shows" taken from the example's own module docstring's first paragraph, the snippet, and the build line):

```markdown
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
```

`docs/examples/allowance_token.md` additionally carries O-DOC2:

```markdown
!!! note "`from_` renders as `from_`"
    Python reserves `from`, so the `Transfer` event's field is `from_`. The
    field name is emitted verbatim into the spec, so generated bindings show
    `from_` where a Rust contract would show `from`. Aliasing is an M2 item.
```

`docs/cli.md`: install line, the exit-code table (0/1/2/3 with one line each, ruling E4), then four sections each rendering a golden inside a ```` ```text ```` fence via `--8<-- "tests/goldens/cli/<name>.help.txt"`, then "What `inspect` adds over `stellar contract info`" (declared vs recomputed protocol, imports with gates, sha256) and "What `doctor` checks" (the row list).

`docs/api.md`:

```markdown
# API reference

The whole authoring surface is `serpent`'s forty public names; a contract
imports from `serpent` and nowhere else (the compiler's SPT2005). The testing
surface is `serpent.testing`.

## `serpent`

::: serpent

## `serpent.testing`

::: serpent.testing
```

`docs/deployments.md`:

```markdown
# Deployments

Every contract this repository has deployed to Stellar testnet, with the
bytes it runs. Read-only simulations against these are the tier-3 fixtures
under `tests/real_host/fixtures/testnet/`.

| Contract | Network | Id | wasm sha256 | Built from | Notes |
|---|---|---|---|---|---|
| Phase 0 spike counter | testnet | `CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI` | see `spikes/spike1/DEPLOY_LOG.md` | `spikes/spike1/contract_src.py` | frozen Phase 0 evidence; the spike1 goldens anchor to it |
| shapes (M1-E2 build) | testnet | `CDEU7Q4DYJVHL2NENDM263KNXOU73RHHWY2BUWBT2HZX6X4BF4FZ7GNW` | `6a9dd13549bac20f2609ab3d74668963b5249a7943dc7f027cdf6c42bec86e33` | `examples/shapes.py` before M1-F Task 0 | `area` traps (the B1 small-Symbol compare); superseded by the M1-end redeploy |
```

Task 11 appends the two M1-end rows.

- [ ] **Step 5: Build strictly, iterate to zero warnings**

`uv run --no-sync mkdocs build --strict`. Expect warnings from `subset.md` (relative links) or mkdocstrings (docstring parsing). A `subset.md` warning is fixed in `src/serpent/compiler/_render_docs.py` and regenerated (never by editing the .md — the drift test guards it); a docstring warning is fixed by the handler option in Step 3, not by editing docstrings. `uv run --no-sync pytest -q tests/unit/test_docs_site.py` → 5 passed.

- [ ] **Step 6: Gates, commit**

```bash
git add mkdocs.yml docs/index.md docs/getting-started.md docs/cli.md docs/api.md docs/examples docs/deployments.md pyproject.toml uv.lock tests/unit/test_docs_site.py
git commit -m "docs: add the mkdocs-material site generated from the code

Examples render from examples/*.py through snippets, the CLI reference
from the --help goldens, the subset page is the generated subset.md, and
the API reference comes from the package docstrings, so no page can
drift from what it describes. strict mode fails the build on a broken
link; a unit test keeps the example pages and the nav in step with the
EXAMPLES inventory. Nothing is published: the site is built, not
deployed."
```

---

### Task 9: CI — the Rust job, the docs job, the tool-install smoke, and plugin dispatch on stellar-cli 28 (O-CI1, O-CI2, F.1.1, F.1.4, F.1.7)

**Files:**
- Modify: `.github/workflows/ci.yml`
- Test: `tests/unit/test_pins.py` (already asserts the pin); a YAML parse + job-shape test appended to `tests/unit/test_pins.py`; every new step's command run LOCALLY in order (CI itself runs only when Elliot pushes, D16)

**Interfaces:**
- Consumes: `docs/testing.md`'s rebuild command; `host/README.md`'s Rust gate; `SERPENT_REQUIRE_REAL_HOST`; the `docs` group (Task 8); `[project.scripts]` (Task 1).
- Produces: jobs `test` (unchanged), `real-host`, `docs`, `cli-install`.

- [ ] **Step 1: The workflow shape test (failing first)**

Append to `tests/unit/test_pins.py`:

```python
def test_ci_has_the_four_jobs_and_the_real_host_switch() -> None:
    import yaml

    workflow = yaml.safe_load(_CI.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    assert set(jobs) == {"test", "real-host", "docs", "cli-install"}
    real_steps = "\n".join(str(step.get("run", "")) for step in jobs["real-host"]["steps"])
    assert "SERPENT_REQUIRE_REAL_HOST=1" in real_steps
    assert "maturin develop --release" in real_steps
    assert "cargo clippy" in real_steps
    assert "--no-sync" in real_steps
    docs_steps = "\n".join(str(step.get("run", "")) for step in jobs["docs"]["steps"])
    assert "mkdocs build --strict" in docs_steps
    cli_steps = "\n".join(str(step.get("run", "")) for step in jobs["cli-install"]["steps"])
    assert "uv tool install" in cli_steps and "stellar serpent doctor" in cli_steps
    assert "stellar plugin ls" in cli_steps
```

Run → FAIL (one job today).

- [ ] **Step 2: The jobs**

Append to `.github/workflows/ci.yml` under `jobs:` (keep `test` exactly as it is):

```yaml
  real-host:
    # M1-G Task 9 (dossier O-CI1; F's U2): the ONLY place the real-host suite
    # is REQUIRED. `SERPENT_REQUIRE_REAL_HOST=1` fails the session if the
    # extension is missing, so this job can never pass vacuously, and the
    # collected-count step below refuses a run that selected too few
    # real_host tests (dossier F.1.4). Python 3.11 only: the extension is
    # abi3-py311 and the pure-Python matrix is the `test` job's.
    name: real host (Rust gate + SERPENT_REQUIRE_REAL_HOST=1)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d # v10.0.1
        with:
          enable-cache: true
          python-version: "3.11"

      - name: Install Rust
        uses: dtolnay/rust-toolchain@<SHA> # <version>, pinned like setup-uv
        with:
          toolchain: "1.97.1"
          components: clippy, rustfmt

      - name: Cache cargo
        uses: Swatinem/rust-cache@<SHA> # <version>
        with:
          workspaces: host

      # Ruling F-E5 / G-D10 order: sync FIRST (it would prune the extension),
      # then build the extension INTO .venv, then never sync again.
      - name: Install dependencies
        run: uv sync --all-groups

      - name: Build the host extension into .venv
        run: VIRTUAL_ENV=$PWD/.venv uvx maturin develop --release --manifest-path host/Cargo.toml

      - name: Rust gate
        working-directory: host
        run: cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test

      - name: Install wasm-tools
        env:
          WASM_TOOLS_VERSION: "1.258.0"
        run: |
          curl --proto '=https' --tlsv1.2 -sSfL -o wasm-tools.tar.gz \
            "https://github.com/bytecodealliance/wasm-tools/releases/download/v${WASM_TOOLS_VERSION}/wasm-tools-${WASM_TOOLS_VERSION}-x86_64-linux.tar.gz"
          tar xzf wasm-tools.tar.gz
          install "wasm-tools-${WASM_TOOLS_VERSION}-x86_64-linux/wasm-tools" /usr/local/bin/wasm-tools
          rm -rf wasm-tools.tar.gz "wasm-tools-${WASM_TOOLS_VERSION}-x86_64-linux"

      - name: The extension is importable from the suite's interpreter
        run: uv run --no-sync python -c "import serpent_host; print(serpent_host.__file__)"

      - name: Enough real-host tests are selected (never a vacuous run)
        run: |
          summary=$(uv run --no-sync pytest -q -m real_host --collect-only -p no:cacheprovider | tail -1)
          echo "$summary"
          collected=$(echo "$summary" | grep -Eo '^[0-9]+' || echo 0)
          test "$collected" -ge 220 || { echo "only $collected real_host tests collected"; exit 1; }

      - name: Test with the real host REQUIRED
        run: SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q

  docs:
    # The site builds strictly (ruling E5). Nothing here publishes: deploying
    # to Pages is an outward action Elliot triggers by hand.
    name: docs (mkdocs build --strict)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d # v10.0.1
        with:
          enable-cache: true
          python-version: "3.11"
      - run: uv sync --all-groups
      - run: uv run --no-sync mkdocs build --strict

  cli-install:
    # Dossier F.1.1 and F.1.7: the plugin must work OUTSIDE the repo venv, and
    # the Stellar CLI must dispatch `stellar serpent` to it -- proven here on
    # stellar-cli 28.0.0 (the plugin convention was probe-verified on 27.1.0).
    name: cli (uv tool install + stellar-cli 28 dispatch)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d # v10.0.1
        with:
          python-version: "3.11"
      - name: Install the plugin as a tool, from this checkout
        run: |
          uv tool install "serpent[spec] @ file://${PWD}"
          echo "$HOME/.local/bin" >> "$GITHUB_PATH"
      - name: Install stellar-cli 28.0.0
        env:
          STELLAR_CLI_VERSION: "28.0.0"
        run: |
          curl --proto '=https' --tlsv1.2 -sSfL -o stellar-cli.tar.gz \
            "https://github.com/stellar/stellar-cli/releases/download/v${STELLAR_CLI_VERSION}/stellar-cli-${STELLAR_CLI_VERSION}-x86_64-unknown-linux-gnu.tar.gz"
          tar xzf stellar-cli.tar.gz
          install stellar /usr/local/bin/stellar
          stellar --version
      - name: The plugin runs from outside the checkout
        working-directory: ${{ runner.temp }}
        run: |
          stellar-serpent doctor
          stellar-serpent build "$GITHUB_WORKSPACE/examples/counter.py" --out counter.wasm
          stellar-serpent inspect counter.wasm
          stellar contract info env-meta --wasm counter.wasm
      - name: The Stellar CLI dispatches to it
        run: |
          stellar plugin ls | grep -Ex ' *serpent'
          stellar serpent doctor
          stellar serpent build examples/bounty_board.py --out /tmp/bb.wasm --quiet
```

Also create `.github/workflows/docs-deploy.yml` — the ONLY way the site reaches GitHub Pages, and only when Elliot triggers it by hand (ruling E5; publishing is a hard stop, D16):

```yaml
# Deploys the docs site to GitHub Pages ON DEMAND ONLY (M1-G ruling E5).
# There is deliberately no push trigger: publishing is an outward action
# Elliot performs by running this workflow from the Actions tab after
# enabling Pages (Settings -> Pages -> Source: GitHub Actions).
name: docs-deploy
on:
  workflow_dispatch:
permissions:
  contents: read
  pages: write
  id-token: write
concurrency:
  group: pages
  cancel-in-progress: false
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d # v10.0.1
        with:
          python-version: "3.11"
      - run: uv sync --all-groups
      - run: uv run --no-sync mkdocs build --strict --site-dir site
      - uses: actions/upload-pages-artifact@<SHA> # <version>
        with:
          path: site
  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@<SHA> # <version>
```

Resolve the two `<SHA>`s here the same way as below (`actions/upload-pages-artifact`, `actions/deploy-pages`: latest release tag → commit SHA). The `test_ci_has_the_four_jobs...` test reads `ci.yml` only; add one assertion to it that `docs-deploy.yml` parses, has `workflow_dispatch` as its ONLY trigger (`set(workflow["on"]) == {"workflow_dispatch"}` — note PyYAML loads the key `on` as the boolean `True`, so read `workflow.get("on", workflow.get(True))`), and contains `deploy-pages`.

Resolve the two `<SHA>`s: `gh api repos/dtolnay/rust-toolchain/commits/master --jq .sha` (comment `# master, <date>`; this action is versioned by toolchain, not release) and `gh api repos/Swatinem/rust-cache/releases/latest --jq .tag_name` then `gh api repos/Swatinem/rust-cache/git/ref/tags/<tag> --jq .object.sha` (dereference an annotated tag if the object type is `tag`). Confirm the stellar-cli tarball's inner path (`tar tzf stellar-cli.tar.gz`) — if the binary is nested, adjust the `install` line. Confirm `uv tool install "serpent[spec] @ file://${PWD}"` locally FIRST: run it in this checkout, then `stellar-serpent doctor` from `/tmp`, then `uv tool uninstall serpent`; if the `@ file://` form is refused, use `uv tool install ".[spec]"` — whichever works is the spelling for both CI and `docs/getting-started.md`'s "from a checkout" note.

- [ ] **Step 3: Run every new step locally, in order**

From the repo root, in a fresh shell: the `real-host` job's commands (skipping the Rust install; the toolchain is local), then the collected-count step (expect ≥ 220: K14's 218 + Task 4's five), then `SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q`; the `docs` job's build; the `cli-install` job's tool install + doctor + build + inspect + `stellar plugin ls | grep -Ex ' *serpent'` + `stellar serpent doctor` (on the local 27.1.0). Record each command's exit code in the task report. Then `uv tool uninstall serpent` so the machine is as found.

- [ ] **Step 4: Gates, commit**

`uv run --no-sync pytest -q tests/unit/test_pins.py` → pass. Four gates.

```bash
git add .github/workflows/ci.yml .github/workflows/docs-deploy.yml tests/unit/test_pins.py
git commit -m "ci: add the real-host, docs, and cli-install jobs

The real-host job builds the PyO3 extension into the synced venv, runs
the cargo gates, refuses a run that collects too few real_host tests,
and runs the suite with SERPENT_REQUIRE_REAL_HOST=1 so it can never pass
vacuously. The docs job builds the site strictly. The cli-install job
installs the plugin as a uv tool from the checkout and drives it through
stellar-cli 28.0.0's plugin dispatch from outside the repository."
```

---

### Task 10: README, the sandbox, spikes/README, docs/testing.md (O-DOC3, O-DOC4, O-CLI2, O-CLI3, U2, E13)

**Files:**
- Modify: `README.md`, `sandbox/README.md`, `sandbox/compile.py`, `spikes/README.md`, `docs/testing.md`, `src/serpent/_host/_protocol.py:14-16` (a comment only)
- Test: the whole suite (the F promise net walks `docs/` and `README.md` is not in it, but `docs/testing.md` is); `uv run --no-sync mkdocs build --strict` (testing.md is a page)

- [ ] **Step 1: `README.md`**

Rewrite the **Status** section:

```markdown
## Status

**M1 is complete** (2026-09): the compiler frontend and WASM emitter for the
M1 type set, the tier-1 `Env` model, the embedded real host, seven examples,
the `stellar-serpent` CLI plugin, the docs site, and one deliberate,
user-approved testnet deployment of the fixed `shapes` and the `bounty_board`
examples (see `docs/deployments.md`). There is no PyPI release: install from
this repository (below). Nothing here is production-ready or API-stable;
M2 (cross-contract calls, crypto host functions, U256/I256, the model gaps
named in `docs/testing.md`) is next.
```

Add after it:

```markdown
## Install and build

```sh
uv tool install "serpent[spec] @ git+https://github.com/ElliotFriend/stellar-serpent-sdk"
stellar serpent doctor                      # or: stellar-serpent doctor
stellar serpent build examples/counter.py   # writes examples/counter.wasm
stellar serpent inspect examples/counter.wasm
stellar contract deploy --wasm examples/counter.wasm --source alice --network testnet
```

`stellar serpent` is the Stellar CLI's plugin dispatch to the `stellar-serpent`
console script; deploy, invoke, and bindings are the stock CLI's. The full
walkthrough is `docs/getting-started.md`; the site is built with
`uv run --group docs mkdocs serve`.
```

In **Architecture at a glance**: the Examples bullet becomes "seven complete contracts (a counter, error codes, structs, events, an allowance-style token, tagged unions and int enums, and a bounty board that touches every M1 surface)"; add a bullet after Emitter: "**CLI** (`serpent/cli.py`) -- `stellar-serpent build|inspect|doctor`; `build` wraps `build_file`, `inspect` reads a module's sections, imports (with protocol gates), and declared-vs-recomputed protocol, `doctor` checks the toolchain offline." Keep the Val codec / chain types / decorators / Env / `_host` / spec / emitter bullets and the "Tagged unions and int enums: the fence" and "Testing" sections VERBATIM. Replace the Phase 0 paragraph's "This repo has no release yet" sentence (now in Status). Final check: `grep -n "in progress\|five\|six complete" README.md` → nothing.

- [ ] **Step 2: `sandbox/`**

`sandbox/compile.py` becomes:

```python
"""Superseded by the CLI (M1-G): `stellar serpent build <contract.py>`.

Kept as a pointer for anyone with the old command in their shell history.
`build_file` is the public API this used to call by hand; the CLI adds the
sha256, the declared-vs-target protocol line, and `--meta`.
"""

import sys

from serpent.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["build", *sys.argv[1:]]))
```

(`uv run python sandbox/compile.py sandbox/counter.py` therefore still works, now through the CLI.) `sandbox/README.md` is rewritten:

```markdown
# Sandbox

A scratch area for playing with serpent. Nothing in here is shipped, tested,
or reviewed -- expand, break, and rewrite freely. `counter.py` and
`hello_world.py` are read by one test (`tests/unit/test_harness_hostfns.py`)
as small whole-contract fixtures, so leave those two as they are and add new
files beside them.

## Quick start

```sh
stellar serpent build sandbox/counter.py      # writes sandbox/counter.wasm
stellar serpent inspect sandbox/counter.wasm
stellar serpent doctor
```

(`uv run python sandbox/compile.py sandbox/counter.py` still works; it just
calls the CLI.) The diagnostics are the interesting part: break `counter.py`
on purpose and rebuild -- each rejection carries an `SPT####` code, a location,
and a rewrite that compiles. The full catalog is `docs/subset.md`.

## Run it without building

```python
from serpent import U32
from serpent.env import Env, deploy
```

A contract's methods run as plain Python against a tier-1 `Env`; the real host
is one import away (`serpent.testing.RealEnv`, `docs/testing.md`). The worked
contracts are `examples/` -- seven of them, each compiled, run at tier 1, under
the mini host, and on the real host by the test suite; `examples/bounty_board.py`
is the one that touches every M1 surface, and it started life in this directory.

## Notes

- Compiling a module executes its top level (the documented build-time trust
  boundary) -- fine for your own code; don't point it at untrusted files.
- This directory is outside the mypy/pytest gates (`ruff check` does lint it).
```

`guestbook.py`, `rolodex.py`, `storage.py` stay untouched (Elliot's scratch).

- [ ] **Step 3: `spikes/README.md` (U2)**

```markdown
# Spikes -- FROZEN Phase 0 evidence

Everything here is the throwaway code of Phase 0 (2026-08-26), kept read-only
as provenance: `spike1/` is the hand-assembled compiler and harness whose
artifact is live on testnet (`DEPLOY_LOG.md`, `ACCEPTANCE.md`), `spike2/` the
PyO3 real-host probe. Sub-plan D superseded the emitter and F the harnesses;
what remains load-bearing is the EVIDENCE -- the goldens under `tests/` cite
these files by line, and the Phase 0 findings
(`docs/superpowers/specs/2026-08-26-phase0-findings.md`) are the reviewed
record. Nothing under `src/` imports from here, `ruff` treats it as historical
(`pyproject.toml`), and the M1-G decision (2026-09-10, with Elliot) was to
retain it unchanged rather than archive it to a tag.
```

- [ ] **Step 4: `docs/testing.md` additions**

Append two sections:

```markdown
## CI's jobs

`test` runs the four gates on Python 3.11/3.12/3.13 without Rust (the
real-host tests skip loudly there). `real-host` builds the extension with
maturin, runs the cargo gates, refuses a run that collects too few
`real_host` tests, and runs the whole suite with
`SERPENT_REQUIRE_REAL_HOST=1`, so a missing extension fails the job instead
of skipping. `docs` builds the site strictly; `cli-install` installs the
plugin as a `uv tool` from the checkout and drives it through stellar-cli's
plugin dispatch from outside the repository.

## Bumping wasm-tools

The pin lives in two places that a unit test keeps equal:
`src/serpent/_pins.py` (`WASM_TOOLS_PIN`, what `stellar-serpent doctor`
compares your local tool against) and `.github/workflows/ci.yml`
(`WASM_TOOLS_VERSION`, what CI installs). To bump: check
https://github.com/bytecodealliance/wasm-tools/releases, edit both, run
`uv run --no-sync pytest -q tests/unit/test_pins.py`, and install the same
version locally. Nothing polls for a newer release on purpose.
```

Also in the "Version pins" table add a row `| wasm-tools | 1.258.0 (src/serpent/_pins.py) |`.

- [ ] **Step 5: O-CLI3 — document the target default**

In `src/serpent/_host/_protocol.py`, extend `DEFAULT_TARGET_PROTOCOL`'s comment: "27 is MAINNET's protocol (testnet is 28 as of 2026-09): the target is an upper bound on what a module may use, so the default keeps a default build deployable on the lowest live network; `stellar serpent build --target-protocol 28` opts in (M1-G ruling E10). Revisit when mainnet moves." No code change.

- [ ] **Step 6: Gates (incl. the docs build), commit**

`uv run --no-sync mkdocs build --strict`; four gates (the F promise net walks `docs/testing.md`; a new "sub-plan F" mention there would fail it — write none).

```bash
git add README.md sandbox/README.md sandbox/compile.py spikes/README.md docs/testing.md src/serpent/_host/_protocol.py
git commit -m "docs: describe the shipped M1 shape in the README, sandbox, spikes, and testing docs

The README states M1 complete with the install and build commands, the
sandbox points at the CLI and names the two files a test reads, spikes/
is documented as retained Phase 0 evidence, and testing.md gains CI's
job map and the wasm-tools bump procedure."
```

---

### Task 11: The M1-end deployment — pre-flight code, the HARD STOP, and the post-deploy record (S9, U1, O-DEP1, O-DEP3, rulings E11/E14/E15)

This task has three halves. **11a** and **11c** are implementer code; **11b** is a checklist Elliot executes with the controller in-session. No implementer runs a chain write.

**Files:**
- 11a Modify: `src/serpent/__init__.py:89`, `pyproject.toml` (`version`), `tests/unit/test_public_api.py:93-94`, `uv.lock`; `tests/real_host/test_testnet_fixtures.py` (table-driven fixture sets)
- 11b: none (chain writes by Elliot; a scratch log)
- 11c Modify: `tests/real_host/fixtures/testnet/shapes/*` (re-recorded), `tests/real_host/fixtures/testnet/README.md`; Create `tests/real_host/fixtures/testnet/bounty_board/{deployed.wasm,total_posted.json,open_ids.json}`; Modify `tests/real_host/test_testnet_fixtures.py` (second set; the flip), `tests/semantics/env_scenarios.py:110-114` (comment only, D17), `docs/deployments.md`, `README.md`, `docs/superpowers/process.md`, `tests/unit/test_no_stale_promises.py` (the G net)

#### 11a — pre-flight code (Sonnet)

- [ ] **Step 1: Version 0.1.0 (ruling E11)**

`src/serpent/__init__.py`: `__version__ = "0.1.0"`; `pyproject.toml`: `version = "0.1.0"`; `tests/unit/test_public_api.py::test_version_string`: `"0.1.0"`. `uv sync --all-groups --inexact` so `importlib.metadata.version("serpent")` agrees (`tests/unit/test_sections.py:1206` and `test_emitter_module.py:1036` pin that). Every golden that embeds `serpentver` moves: regenerate `tests/goldens/wasm/*.wat.txt` with `SERPENT_REGEN_GOLDENS=1 uv run --no-sync pytest -q tests/unit/test_emitter_printer.py` and confirm the diff is ONLY the `serpentver` bytes in each data/meta section. `git diff --stat` must list the goldens and nothing unexpected.

- [ ] **Step 2: Table-driven fixture sets (behavior-preserving refactor, failing-first by construction)**

In `tests/real_host/test_testnet_fixtures.py`, replace the module-level `FIXTURE_DIR`/`DEPLOYED`/`FIXTURES`/`CONTRACT_ID`/`DEPLOYED_SHA256`/`B1_DIVERGENCE` constants with:

```python
@dataclass(frozen=True)
class FixtureSet:
    """One deployed contract's recorded corpus (dossier D.6 step 4)."""

    name: str
    directory: Path
    contract_id: str
    deployed_sha256: str
    example: Path
    ctor: tuple[Any, ...]
    #: Declared three-way divergences, `method -> tier-1 answer` (B1's `area`).
    divergences: dict[str, object]

    @property
    def deployed(self) -> Path:
        return self.directory / "deployed.wasm"

    @property
    def fixtures(self) -> list[Fixture]:
        return fixtures_under(self.directory)


_TESTNET = Path(__file__).parent / "fixtures" / "testnet"

SHAPES = FixtureSet(
    name="shapes",
    directory=_TESTNET / "shapes",
    contract_id="CDEU7Q4DYJVHL2NENDM263KNXOU73RHHWY2BUWBT2HZX6X4BF4FZ7GNW",
    deployed_sha256="6a9dd13549bac20f2609ab3d74668963b5249a7943dc7f027cdf6c42bec86e33",
    example=EXAMPLE_SHAPES,
    ctor=(),
    divergences={"area": U32(10)},
)
SETS: tuple[FixtureSet, ...] = (SHAPES,)
```

and parametrize `test_the_fixtures_were_recorded_against_the_deployed_bytes`, `test_every_committed_fixture_round_trips_through_the_recorded_json`, and `test_the_real_host_and_tier_1_agree_with_testnet` over `SETS` (the last over `[(s, f) for s in SETS for f in s.fixtures]` with ids `f"{s.name}:{f.method}"`), reading `set.contract_id`/`set.deployed_sha256`/`set.divergences` where the module constants were read. `test_this_trees_shapes_build_differs_from_the_deployed_bytes_until_the_next_deploy` stays as is (it flips in 11c). Run `SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q tests/real_host/test_testnet_fixtures.py` → the same pass count as before the refactor.

- [ ] **Step 3: Gates, commit**

```bash
git add src/serpent/__init__.py pyproject.toml uv.lock tests/unit/test_public_api.py tests/goldens/wasm tests/real_host/test_testnet_fixtures.py
git commit -m "chore: bump serpent to 0.1.0 and table-drive the tier-3 fixture sets

The M1-end deployment artifacts carry serpentver 0.1.0, so the bump
lands before they are built. The tier-3 module now reads one FixtureSet
per deployed contract so the bounty board's recordings join beside the
shapes corpus after the deployment."
```

#### 11b — THE HARD STOP (Elliot + controller; nothing here is an implementer's)

- [ ] **Pre-flight, read-only, from main at the 11a commit:**
  1. `SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q` green; `uv run --no-sync stellar-serpent doctor` clean (`stellar config migrate` first if the CLI still flags the old config, K11).
  2. `uv run --no-sync stellar-serpent build examples/shapes.py --out /tmp/m1/shapes.wasm` and `... examples/bounty_board.py --out /tmp/m1/bounty_board.wasm`; record both sha256s and `inspect` output (declared 20 / 22, no MISMATCH, `serpentver=0.1.0` in meta).
  3. Identity: Elliot names one from `stellar keys ls`; `stellar keys address <id>` prints the `G...` that becomes the board's on-chain admin. Balance is checked READ-ONLY at `https://stellar.expert/explorer/testnet/account/<G...>` (never `stellar keys fund`, which is a write); a deploy that fails for lack of balance is loud and harmless, and funding is Elliot's call.
- [ ] **Elliot approves, explicitly, in-session.** The controller writes the approval sentence into the SDD ledger with the date.
- [ ] **Deploy (Elliot runs; each output pasted into the ledger):**

```sh
stellar contract deploy --wasm /tmp/m1/shapes.wasm --source <id> --network testnet
stellar contract deploy --wasm /tmp/m1/bounty_board.wasm --source <id> --network testnet -- --admin <G...>
```

- [ ] **Optional seeding, same approved session (E15 / F.1.10):** reproduce the state the old shapes fixtures were recorded against, so `area` answers `U32(10)`: `stellar contract invoke --id <SHAPES2> --source <id> --network testnet -- draw_rect --w 5 --h 2` then `-- pin` (check the argument names with `stellar contract invoke ... -- draw_rect --help`). For the board: `-- post --poster <G...> --reward 50 --priority 2` if Elliot wants a non-empty board recorded.
- [ ] **Fidelity:** `stellar contract info hash --id <C...> --network testnet` == the local sha256, for both. `stellar contract fetch --id <C...> --network testnet --out-file tests/real_host/fixtures/testnet/<set>/deployed.wasm` for both.

#### 11c — post-deploy record (Sonnet; needs 11b's ids)

- [ ] **Step 1: Re-record**

```sh
SERPENT_TESTNET_RECORD=1 uv run --no-sync python -m serpent.testing.testnet record \
    --contract <SHAPES2> --out tests/real_host/fixtures/testnet/shapes kind palette is_pinned area
SERPENT_TESTNET_RECORD=1 uv run --no-sync python -m serpent.testing.testnet record \
    --contract <BOARD> --out tests/real_host/fixtures/testnet/bounty_board total_posted open_ids
```

(The recorder refuses a wasm-hash mismatch against the chain's instance, C10.) Delete the old shapes `*.json` first so no stale fixture survives.

- [ ] **Step 2: The module**

`SHAPES` gets the new `contract_id`/`deployed_sha256` and `divergences={}` (B1 retired); add

```python
BOUNTY_BOARD = FixtureSet(
    name="bounty_board",
    directory=_TESTNET / "bounty_board",
    contract_id="<BOARD>",
    deployed_sha256="<sha>",
    example=EXAMPLE_BOUNTY_BOARD,
    ctor=(Address("<the admin G...>"),),
    divergences={},
)
SETS = (SHAPES, BOUNTY_BOARD)
```

(the tier-1 leg deploys with `ctor`; an account-strkey admin is fine at tier 1 — `Env(auths=...)` is not involved in a read). Flip the differs test:

```python
@pytest.mark.parametrize("fixture_set", SETS, ids=lambda s: s.name)
def test_this_trees_build_equals_the_deployed_bytes(fixture_set: FixtureSet) -> None:
    """Retired B1: since the M1-end deployment (2026-09, docs/deployments.md)
    HEAD's build of each deployed example IS the deployed artifact, byte for
    byte -- Phase 0's fidelity rule, now for every fixture set."""
    built = build_file(fixture_set.example).wasm
    assert hashlib.sha256(built).hexdigest() == fixture_set.deployed_sha256
```

Update the module docstring's B1 paragraph to past tense; `tests/real_host/fixtures/testnet/README.md`'s `area.json` section becomes a historical note and its table gains the board. Run `SERPENT_REQUIRE_REAL_HOST=1 uv run --no-sync pytest -q tests/real_host/test_testnet_fixtures.py` → green, `area` now agrees three ways.

- [ ] **Step 3: The record, the prose, the G net**

`docs/deployments.md`: two rows (`shapes (M1 close)`, `bounty_board (M1 close)`) with ids, shas, "Built from `examples/<x>.py` at <commit>", ledger and date from the fixtures' headers. `README.md` Status: the sentence already says it; add the two ids. `docs/superpowers/process.md` State: "M1 COMPLETE (2026-09-xx): ... NEXT: M2 (dossier from the C/D/E/E2/F/G attention files' M2 items)". `tests/semantics/env_scenarios.py:110-114`: the `_ADMIN` comment says "a real decodable contract strkey (the FIRST shapes deployment's id, kept as an opaque address after the M1-end redeploy)". The fourth promise net in `tests/unit/test_no_stale_promises.py`, mirroring the F net (needles `"sub-plan g"`, `"m1-g"`, `"(g)"`; walk `_WALKED_F`; text-keyed allowlist seeded with `tests/unit/test_address.py`'s "Neither an account (G) nor a contract (C) strkey", `tests/unit/test_decorators.py`'s "(g) Pinned so a future edit", and the two "sub-plan G's wave 1" history comments; a teeth test). Every other live "(G)" mention must be gone by now (the fixtures README, the testnet test) — the net proves it.

- [ ] **Step 4: Gates, commit, tag (controller)**

```bash
git add tests/real_host tests/semantics/env_scenarios.py tests/unit/test_no_stale_promises.py docs/deployments.md README.md docs/superpowers/process.md
git commit -m "test(tier3): record the M1-end deployments and retire the B1 divergence

The fixed shapes contract and the bounty board are deployed to testnet;
their fixtures are re-recorded against the fetched bytes, HEAD's build of
each example equals its deployed artifact, area agrees three ways, and
the deployment record names both contracts. A fourth promise net keeps
sub-plan G from being cited as a future."
git tag -a v0.1.0 -m "serpent 0.1.0: M1 complete"   # LOCAL; Elliot pushes (D16)
```

---

## Carried F minors (ruling E14: which G takes)

The M1-F final review left nineteen deferred minors (F ledger `minor (deferred)` lines; `final-review.md`'s triage). G takes NONE of them as code, for these reasons, and the attention file carries each forward with the reason:

- `_member_for` first-match across error enums (F T3): only ambiguous when two `@contracterror` classes in one contract share a code, a shape no M1 example has; an honest fix is a loud refusal in `serpent.testing` — an M2 testing-surface item, decided with the M2 dossier.
- Instance-bucket `ttl`/`live_until` for an absent key (F T3), `map_del` on a missing key, the `_innermost_error` two-distinct-errors probe: HOST-FACT candidates (they need a `HOST_FACTS` row and a real-host measurement), M2.
- Duplicate Symbol keys in an `ScMap` collapsing silently in `_scval` (F T2): the host refuses a duplicate-keyed map at the boundary, so the collapse is unreachable from a deployed contract; an M2 `_scval` hardening.
- Per-site inlined guard cost (F T0), `_bool_from_flag` reuse (T0), `every_type_is_named` asserting `type_name` only (T1), commit subjects over 72 characters (T5/T6): cosmetic or process; recorded, not acted on.

Everything G DOES act on from earlier attention files is in Tasks 5–7 and 10 (O-HYG1–O-HYG8, O-DOC2/3/4, O-CLI2/3, O-MOCK1, U2). O-HYG9 (the surface-denial gate's whole-line exemption and vocabulary gaps, zero live instances) is carried to M2 with the E2 attention file's wording.

## Completion (process, not tasks)

1. **Attention file** `.superpowers/sdd/2026-09-10-m1g-cli-and-ship/final-review-attention.md`, reconciled against the ledger: every inventory and golden that moved (Tasks 4, 7, 11a), the E2 plan-author correction, the strict-mock default flip, every registry string edited, the new bridge rules and `_UNBRIDGED_BY_DESIGN` entries with reasons, the CI job commands' local exit codes, the deployment ids and shas, deferred minors per task, and obligations carried to M2/M3 (the dossier §A.6's M2/M3 list plus anything new).
2. **Fable final whole-branch review** on `main..m1g-cli-and-ship`, fed the attention file; one fix wave; scoped re-review; local merge (fast-forward if possible). No push.
3. **decisions.md**: the plan-review rulings entry; ratification (or overturn) of the E2 correction; the final-review rulings entry.
4. **process.md**: the State section (M1 COMPLETE; the two contract ids; suite counts; carried obligations pointer); NEXT: M2.
5. **Memory**: update `project_serpent_python_soroban_sdk.md`'s "How to apply" if the pickup path changes (it should not: process.md remains the entry point).

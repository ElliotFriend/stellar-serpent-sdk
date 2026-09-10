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
from pathlib import Path
from types import ModuleType
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


def test_serpent_host_absent_is_info_with_a_rebuild_pointer() -> None:
    """With `serpent.testing._marker` importable (the dev venv) the remedy IS
    `REBUILD_COMMAND`; on a bare install it is the docs pointer. Both name where
    to look; neither is a third copy of the maturin command [m6]."""
    row = _row(cli.run_doctor(_probes(), None), "serpent_host")
    assert row.status == "info"
    assert "maturin develop" in row.remedy or "docs/testing.md" in row.remedy


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


def test_doctor_human_output_names_every_row_once(capsys: pytest.CaptureFixture[str]) -> None:
    """[m13] one `[status] name` line per row, in order; remedy lines are indented."""
    cli.main(["doctor"])
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("[")]
    names = [line.split("]", 1)[1].split("  ")[0].strip() for line in lines]
    assert names == [c.name for c in cli.run_doctor(cli.Probes(), None)]


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

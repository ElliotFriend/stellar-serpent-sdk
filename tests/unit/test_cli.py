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


# --- build ----------------------------------------------------------------------------

from tests.unit.test_emitter_end_to_end import EXAMPLES as ALL_EXAMPLES

_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = _ROOT / "examples"


def test_build_help_golden() -> None:
    golden("build", cli.subparser("build").format_help())


@pytest.mark.parametrize("path", ALL_EXAMPLES, ids=lambda p: p.stem)
def test_build_writes_the_same_bytes_build_file_returns(
    path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """[M9] over EVERY example: the sha256 the CLI prints is the on-chain wasm
    hash Task 11b compares against `stellar contract info hash`, so the claim
    "the CLI writes what build_file returns" is proven for the constructor,
    memory, and union/enum paths, not sampled."""
    import hashlib

    from serpent.emitter import build_file

    out = tmp_path / f"{path.stem}.wasm"
    assert cli.main(["build", str(path), "--out", str(out)]) == cli.EXIT_OK
    expected = build_file(path).wasm
    assert out.read_bytes() == expected
    printed = capsys.readouterr().out
    assert hashlib.sha256(expected).hexdigest() in printed
    assert "declared protocol" in printed


def test_build_default_out_is_beside_the_source(tmp_path: Path) -> None:
    source = tmp_path / "c.py"
    source.write_text((EXAMPLES / "counter.py").read_text(encoding="utf-8"), encoding="utf-8")
    assert cli.main(["build", str(source), "--quiet"]) == cli.EXIT_OK
    assert (tmp_path / "c.wasm").exists()


def test_build_json_carries_the_facts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "e.wasm"
    assert (
        cli.main(["build", str(EXAMPLES / "errors.py"), "--out", str(out), "--json"]) == cli.EXIT_OK
    )
    facts = json.loads(capsys.readouterr().out)
    assert facts["declared_protocol"] == 22  # a constructor-bearing example (D9)
    assert facts["target_protocol"] is None
    assert facts["bytes"] == out.stat().st_size
    assert sorted(facts) == sorted(
        [
            "source",
            "out",
            "bytes",
            "sha256",
            "declared_protocol",
            "target_protocol",
            "imports",
            "exports",
            "runtime_parts",
            "memory",
            "wasm_tools",
        ]
    )


def test_build_rejection_renders_diagnostics_and_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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


def test_build_missing_source_is_an_environment_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["build", str(tmp_path / "nope.py")]) == cli.EXIT_ENVIRONMENT
    assert "nope.py" in capsys.readouterr().err


def test_build_meta_pairs_land_in_contractmetav0(tmp_path: Path) -> None:
    from serpent.spec.decode import decode_meta
    from tests.unit.test_sections import _wasm_custom_section

    out = tmp_path / "m.wasm"
    assert (
        cli.main(
            [
                "build",
                str(EXAMPLES / "counter.py"),
                "--out",
                str(out),
                "--meta",
                "team=devrel",
                "--meta",
                "tag=v1",
                "--quiet",
            ]
        )
        == cli.EXIT_OK
    )
    pairs = dict(decode_meta(_wasm_custom_section(out.read_bytes(), "contractmetav0")))
    assert pairs["team"] == "devrel" and pairs["tag"] == "v1"


def test_build_reserved_meta_key_is_an_environment_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(
            [
                "build",
                str(EXAMPLES / "counter.py"),
                "--out",
                str(tmp_path / "x.wasm"),
                "--meta",
                "serpentver=9",
            ]
        )
        == cli.EXIT_ENVIRONMENT
    )
    assert "reserved" in capsys.readouterr().err


def test_build_meta_without_equals_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["build", "x.py", "--meta", "novalue"])
    assert info.value.code == cli.EXIT_USAGE


def test_build_target_protocol_below_a_constructor_floor_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(
        [
            "build",
            str(EXAMPLES / "errors.py"),
            "--out",
            str(tmp_path / "e.wasm"),
            "--target-protocol",
            "21",
        ]
    )
    assert code == cli.EXIT_REJECTED
    assert "SPT6001" in capsys.readouterr().err


def test_build_require_external_validate_without_the_tool_is_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    code = cli.main(
        [
            "build",
            str(EXAMPLES / "counter.py"),
            "--out",
            str(tmp_path / "c.wasm"),
            "--external-validate",
            "require",
        ]
    )
    assert code == cli.EXIT_ENVIRONMENT
    assert "wasm-tools" in capsys.readouterr().err


def test_build_without_the_spec_extra_names_the_hint(tmp_path: Path) -> None:
    """A bare install, simulated in a SUBPROCESS [m22] (the pattern
    `test_core_zero_dep.py::test_importing_serpent_does_not_load_stellar_sdk`
    uses): a meta-path finder that refuses `stellar_sdk`, then `cli.main`."""
    probe = (
        "import sys, importlib.abc\n"
        "class Refuse(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path, target=None):\n"
        "        if name == 'stellar_sdk' or name.startswith('stellar_sdk.'):\n"
        "            raise ModuleNotFoundError(name, name=name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Refuse())\n"
        "from serpent import cli\n"
        f"raise SystemExit(cli.main(['build', {str(EXAMPLES / 'counter.py')!r}, '--out', {str(tmp_path / 'c.wasm')!r}]))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert done.returncode == cli.EXIT_ENVIRONMENT, done.stderr
    assert cli.SPEC_EXTRA_HINT in done.stderr


# --- inspect --------------------------------------------------------------------------


def test_inspect_help_golden() -> None:
    golden("inspect", cli.subparser("inspect").format_help())


def test_inspect_prints_declared_and_recomputed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "e.wasm"
    build = ["build", str(EXAMPLES / "errors.py"), "--out", str(out), "--quiet"]
    assert cli.main(build) == cli.EXIT_OK
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


def test_inspect_wat_appends_a_disassembly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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

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
import hashlib
import importlib
import json
import shutil
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, cast

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
#: Deliberately NOT a third spelling of the maturin command (`_marker.REBUILD_COMMAND`
#: and host/README.md are asserted byte-identical; a copy here would drift) [m6].
HOST_HINT_FALLBACK = "build the host extension (docs/testing.md, 'Building the extension')"
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


def _python_version() -> tuple[int, int, int]:
    """This interpreter's `(major, minor, micro)`, typed as a fixed-length tuple
    (`sys.version_info[:3]` is only `tuple[int, ...]` to mypy)."""
    major, minor, micro = sys.version_info[:3]
    return (major, minor, micro)


@dataclass(frozen=True)
class Probes:
    """The seam between `run_doctor` and this machine, so tests script every
    answer instead of depending on what is installed here."""

    which: Callable[[str], str | None] = shutil.which
    import_module: Callable[[str], ModuleType] = importlib.import_module
    run: Callable[[list[str]], str] = dataclasses.field(default=lambda argv: _run(argv))
    python_version: tuple[int, int, int] = dataclasses.field(default_factory=_python_version)


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
    with urllib.request.urlopen(
        request, timeout=20
    ) as response:  # fixed https hosts, not user input
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
            Check(
                "python", "fail", f"{major}.{minor}.{micro}", "serpent needs Python 3.11 or newer"
            )
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
        checks.append(
            Check("plugin on PATH", "warn", "stellar-serpent not on PATH", PLUGIN_PATH_HINT)
        )
    else:
        checks.append(Check("plugin on PATH", "ok", plugin))

    try:
        probes.import_module("serpent_host")
    except ImportError:
        checks.append(
            Check(
                "serpent_host",
                "info",
                "not built (needed only for real-host tests)",
                _rebuild_command(),
            )
        )
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


def _parse_meta(pair: str) -> tuple[str, str]:
    """`KEY=VALUE` for `build --meta`; anything else is argparse's usage error (exit 2)."""
    key, sep, value = pair.partition("=")
    if not sep or not key:
        raise argparse.ArgumentTypeError(f"--meta wants KEY=VALUE, got {pair!r}")
    return key, value


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


# --- build (inspect's body lands in Task 3) ---------------------------------------------


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
    meta: dict[str, str] = dict(args.meta)  # already (key, value) pairs: `type=_parse_meta` [M15]
    validate_external: bool | None = {"auto": None, "require": True, "skip": False}[
        args.external_validate
    ]

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
        external = "skipped (--external-validate skip)"
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
        type=_parse_meta,  # a malformed pair is a usage error (exit 2) [M15]
        help="a contractmetav0 pair; repeatable",
    )
    build.add_argument(
        "--version",
        dest="contract_version",
        metavar="STR",
        help="the contract's own `version` meta entry (not serpent's version)",  # [m21]
    )
    build.add_argument(
        "--target-protocol",
        type=int,
        metavar="N",
        help="declare exactly protocol N; a host function gated above N is a compile error",
    )
    # One tri-state flag rather than a mutually-exclusive pair: argparse wraps a
    # mutually-exclusive group differently on Python 3.13, which would make the
    # --help golden interpreter-dependent across CI's own matrix [B2].
    build.add_argument(
        "--external-validate",
        choices=("auto", "require", "skip"),
        default="auto",
        help="wasm-tools: auto = run it when installed (default); require = fail if absent; skip = never",
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
    for action in parser._actions:  # argparse offers no public accessor for this
        if isinstance(action, argparse._SubParsersAction):
            # typeshed's `_SubParsersAction.choices` is generic over the parser
            # type; unparametrized here, so mypy sees `Any` -- cast to the type
            # `build_parser` actually put there.
            return cast(argparse.ArgumentParser, action.choices[name])
    raise KeyError(name)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run: Callable[[argparse.Namespace], int] = args.run
    return run(args)


if __name__ == "__main__":  # pragma: no cover -- `python -m serpent.cli`
    raise SystemExit(main())

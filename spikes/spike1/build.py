"""Spike 1 driver: contract source -> validated Soroban wasm.

The order of operations is the point: compile, assemble, **validate**, and only
then write. An invalid module never reaches the ``-o`` path, so "the file
exists" and "the file is a valid contract" are the same statement.

    uv run python spikes/spike1/build.py spikes/spike1/contract_src.py \\
        -o spikes/spike1/spike.wasm
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import tempfile

from emitter import HOST_FN_NAMES, emit_module, load_host_fns, protocol_floor
from frontend import parse_contract

SPIKE_DIR = pathlib.Path(__file__).parent

# The protocol this build declares in contractenvmetav0. Separate from the
# floor computed off the imports: the floor is what the code *needs*, this is
# what the build *targets*.
TARGET_PROTOCOL = 27
# Soroban's launch protocol. env.json annotates a host function with
# `min_supported_protocol` only when it arrived *after* the launch set, so an
# unannotated import floors at this.
BASE_PROTOCOL = 20

# wasm-tools' feature set must match what the Soroban host accepts: the
# baseline plus the three extensions the host enables. `-all` first turns
# everything off, so anything not listed here is rejected.
WASM_FEATURES = "-all,mutable-global,sign-extension,bulk-memory"


def validate(wasm: bytes) -> None:
    """Run ``wasm-tools validate`` over the bytes; raise ``SystemExit`` on failure."""
    with tempfile.NamedTemporaryFile(suffix=".wasm") as tmp:
        tmp.write(wasm)
        tmp.flush()
        proc = subprocess.run(
            ["wasm-tools", "validate", f"--features={WASM_FEATURES}", tmp.name],
            capture_output=True,
            text=True,
            check=False,
        )
    if proc.returncode != 0:
        sys.stderr.write("wasm-tools validate failed; no file was written\n")
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", help="contract source file")
    ap.add_argument("-o", "--output", required=True, help="wasm output path")
    ap.add_argument(
        "--env-json",
        default=str(SPIKE_DIR / "env.json"),
        help="vendored rs-soroban-env env.json (pinned at v28.0.2)",
    )
    args = ap.parse_args(argv)

    ir = parse_contract(args.source)
    host_fns = load_host_fns(pathlib.Path(args.env_json), HOST_FN_NAMES)
    floor = protocol_floor(host_fns, BASE_PROTOCOL)

    wasm = emit_module(
        ir,
        host_fns,
        protocol=TARGET_PROTOCOL,
        meta_pairs={"serpent": "0.0.1-spike1"},
    )
    validate(wasm)

    out = pathlib.Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(wasm)

    print(
        f"{args.source} -> {out}: {len(wasm)} bytes, "
        f"exports={[f.name for f in ir.functions]}, "
        f"env-meta protocol={TARGET_PROTOCOL} (import floor={floor}), "
        f"validated with --features={WASM_FEATURES}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

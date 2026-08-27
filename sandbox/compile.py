"""Compile a sandbox contract and show what the frontend produced.

Usage (from the repo root):

    uv run python sandbox/compile.py sandbox/counter.py
"""

import sys
from pathlib import Path

from serpent.compiler import CompileError, compile_module


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "sandbox/counter.py")
    source = path.read_text()

    try:
        out = compile_module(source, str(path))
    except CompileError as exc:
        print(f"REJECTED with {len(exc.diagnostics)} diagnostic(s):\n")
        print(exc.render(source.splitlines()))
        return 1

    print(f"COMPILED {path}\n")
    print(f"  declared protocol : {out.declared_protocol}")
    print(f"  needs memory      : {out.needs_memory}")
    print(f"  host fns used     : {', '.join(sorted(out.host_fns_used))}")
    reachable_only = sorted(out.host_fns_reachable - out.host_fns_used)
    if reachable_only:
        print(f"  reachable extras  : {', '.join(reachable_only)} (D picks between forms)")
    if out.runtime_parts_needed:
        print(f"  runtime parts     : {', '.join(sorted(out.runtime_parts_needed))}")
    print("  functions         :")
    for fn in out.functions:
        params = ", ".join(f"{name}: {ty.repr_form.name}" for name, ty, _loc in fn.params)
        print(f"    {fn.export_name}({params}) -> {fn.ret.repr_form.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

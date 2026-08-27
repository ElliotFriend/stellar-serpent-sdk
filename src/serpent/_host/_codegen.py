"""env.json -> bindings.py codegen.

Reads the pinned `env.json`, renders `HOST_FUNCTIONS` as a literal Python
tuple (no runtime JSON parsing in the shipped module -- import-time cost
stays trivial and the generated file is diffable), pipes the result through
`ruff format -` so the checked-in file is format-stable by construction, and
writes it out.

Run as `uv run python -m serpent._host._codegen [--out PATH]`; with no
`--out`, it overwrites the checked-in `bindings.py` next to this file.
`tests/unit/test_host_bindings.py::test_bindings_regeneration_is_byte_identical`
regenerates into a temp path and diffs it against the checked-in copy, so
drift is a test failure, not a silent surprise.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from serpent._host._model import ENV_TYPE_TO_WASM_TYPE

#: rs-soroban-env tag this env.json is pinned to, and the upstream git blob
#: sha `tests/unit/test_host_bindings.py::test_env_json_matches_upstream_blob`
#: checks it against. Named in the generated header so the pin is visible
#: without cross-referencing the test.
PINNED_TAG = "v28.0.2"
UPSTREAM_BLOB_SHA = "f9c50fc25c8f32cdc0a6d6f465d3b14143d446e3"

#: Pin docstring hygiene, asserted while generating (not at import time):
#: env.json's `docs` values are ASCII, contain no double-quote character, and
#: are at most this many characters. None of this is load-bearing for the
#: `repr()`-based rendering below (which handles arbitrary escaping) -- it is
#: a documented invariant about *this* pin, so a future re-pin that breaks it
#: fails loudly here instead of silently.
MAX_DOCS_LEN = 843

_HERE = Path(__file__).parent
_ENV_JSON_PATH = _HERE / "env.json"
_DEFAULT_OUT_PATH = _HERE / "bindings.py"


class CodegenError(ValueError):
    """Raised when env.json fails a pin-hygiene or exhaustiveness check."""


def _find_pyproject_toml(start: Path) -> Path:
    """Walk up from `start` to find the repo's `pyproject.toml`.

    `ruff format`'s own config discovery walks up from the *target* file, so
    it silently picks up a different (default) line-length when `--out`
    points outside the repo (e.g. a pytest `tmp_path`), breaking
    byte-identical regeneration. Resolving the config explicitly, relative to
    this script's own location rather than the (possibly out-of-repo) `--out`
    path, keeps formatting deterministic regardless of where the caller asks
    for the output to be written.
    """
    for candidate in (start, *start.parents):
        pyproject = candidate / "pyproject.toml"
        if pyproject.is_file():
            return pyproject
    raise CodegenError(f"no pyproject.toml found above {start}")


_HEADER = f'''"""GENERATED -- do not edit.

Produced by `python -m serpent._host._codegen` from the pinned `env.json`
(rs-soroban-env tag {PINNED_TAG}, upstream git blob sha
{UPSTREAM_BLOB_SHA}). Regenerate rather than hand-editing:

    uv run python -m serpent._host._codegen

`tests/unit/test_host_bindings.py::test_bindings_regeneration_is_byte_identical`
fails if this file drifts from a fresh run.

Imports only stdlib and `serpent._host._model` (see that module's docstring
for why: this file and `_model.py` must never form an import cycle with
`serpent._host/__init__.py`).
"""'''


def _load_functions(env_json_path: Path) -> list[dict[str, Any]]:
    """Flatten env.json's modules into a declaration-ordered function list.

    Each returned dict carries the module's export code under `"_module"`
    alongside the function's own keys.
    """
    doc = json.loads(env_json_path.read_text(encoding="utf-8"))
    functions: list[dict[str, Any]] = []
    for module in doc["modules"]:
        for fn in module["functions"]:
            functions.append({**fn, "_module": module["export"]})
    return functions


def _validate_docs(functions: list[dict[str, Any]]) -> None:
    for fn in functions:
        docs = fn.get("docs", "")
        if not docs.isascii():
            raise CodegenError(f"{fn['name']!r}: docs contains non-ASCII characters")
        if '"' in docs:
            raise CodegenError(f"{fn['name']!r}: docs contains a double-quote character")
        if len(docs) > MAX_DOCS_LEN:
            raise CodegenError(
                f"{fn['name']!r}: docs is {len(docs)} chars, exceeds the {MAX_DOCS_LEN}-char pin limit"
            )


def _validate_type_vocabulary(functions: list[dict[str, Any]]) -> None:
    """The exhaustive `_model.ENV_TYPE_TO_WASM_TYPE` key set must exactly equal
    the arg/return type vocabulary actually observed in env.json."""
    observed: set[str] = set()
    for fn in functions:
        observed.update(arg["type"] for arg in fn["args"])
        observed.add(fn["return"])
    known = set(ENV_TYPE_TO_WASM_TYPE)
    unrecognized = observed - known
    unused = known - observed
    if unrecognized:
        raise CodegenError(
            f"env.json uses type(s) absent from _model.ENV_TYPE_TO_WASM_TYPE: "
            f"{sorted(unrecognized)} -- add them to the exhaustive type table"
        )
    if unused:
        raise CodegenError(
            f"_model.ENV_TYPE_TO_WASM_TYPE has entries never observed in env.json: "
            f"{sorted(unused)} -- the table's key set must exactly equal the observed set"
        )


def _render_host_fn(fn: dict[str, Any]) -> str:
    arg_names = tuple(arg["name"] for arg in fn["args"])
    arg_types = tuple(arg["type"] for arg in fn["args"])
    return (
        "    HostFn(\n"
        f"        name={fn['name']!r},\n"
        f"        module={fn['_module']!r},\n"
        f"        export={fn['export']!r},\n"
        f"        arity={len(fn['args'])!r},\n"
        f"        arg_names={arg_names!r},\n"
        f"        arg_types={arg_types!r},\n"
        f"        ret_type={fn['return']!r},\n"
        f"        min_protocol={fn.get('min_supported_protocol')!r},\n"
        f"        max_protocol={fn.get('max_supported_protocol')!r},\n"
        f"        docs={fn.get('docs', '')!r},\n"
        "    ),\n"
    )


def render(functions: list[dict[str, Any]]) -> str:
    body = "".join(_render_host_fn(fn) for fn in functions)
    return (
        f"{_HEADER}\n\n"
        "from serpent._host._model import HostFn\n\n"
        "HOST_FUNCTIONS: tuple[HostFn, ...] = (\n"
        f"{body}"
        ")\n"
    )


def _ruff_format(source: str, *, stdin_filename: Path, config: Path) -> str:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--config",
            str(config),
            "--stdin-filename",
            str(stdin_filename),
            "-",
        ],
        input=source,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def generate(env_json_path: Path = _ENV_JSON_PATH, *, out_path: Path) -> None:
    functions = _load_functions(env_json_path)
    if len(functions) != 199:
        raise CodegenError(f"expected 199 host functions, env.json has {len(functions)}")
    _validate_docs(functions)
    _validate_type_vocabulary(functions)
    source = render(functions)
    config = _find_pyproject_toml(_HERE)
    formatted = _ruff_format(source, stdin_filename=out_path, config=config)
    out_path.write_text(formatted, encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_OUT_PATH,
        help="output path for the generated bindings module (default: the checked-in path)",
    )
    args = parser.parse_args(argv)
    generate(out_path=args.out)


if __name__ == "__main__":
    main()

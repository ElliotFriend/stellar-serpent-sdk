import hashlib
import json
import pathlib
import subprocess
import sys

from serpent._host import HOST_FUNCTIONS, RAW_SCALAR_TYPES, functions_by_name
from serpent._host._model import ENV_TYPE_TO_WASM_TYPE

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
    assert counts == {
        "x": 10,
        "i": 52,
        "m": 14,
        "v": 19,
        "l": 21,
        "d": 2,
        "b": 26,
        "c": 37,
        "a": 12,
        "t": 2,
        "p": 4,
    }


def test_known_exports_resolved_by_name() -> None:
    # Verified in Phase 0 against the live network
    assert (
        functions_by_name["put_contract_data"].module,
        functions_by_name["put_contract_data"].export,
    ) == ("l", "_")
    assert (
        functions_by_name["map_new_from_linear_memory"].module,
        functions_by_name["map_new_from_linear_memory"].export,
    ) == ("m", "9")
    assert (
        functions_by_name["fail_with_error"].module,
        functions_by_name["fail_with_error"].export,
    ) == ("x", "5")
    assert (
        functions_by_name["symbol_new_from_linear_memory"].module,
        functions_by_name["symbol_new_from_linear_memory"].export,
    ) == ("b", "j")


def test_raw_scalar_args_distinguished() -> None:
    put = functions_by_name["put_contract_data"]
    # (key Val, value Val, StorageType raw scalar)
    assert put.val_typed_args == (True, True, False)
    assert "StorageType" in RAW_SCALAR_TYPES


def test_bindings_regeneration_is_byte_identical(tmp_path: pathlib.Path) -> None:
    out = tmp_path / "bindings.py"
    subprocess.run(
        [sys.executable, "-m", "serpent._host._codegen", "--out", str(out)],
        check=True,
        capture_output=True,
        text=True,
    )
    committed = (ENV_JSON.parent / "bindings.py").read_bytes()
    assert out.read_bytes() == committed


def test_wasm_types_uniform_i64_at_this_pin() -> None:
    for fn in HOST_FUNCTIONS:
        assert all(t == "i64" for t in fn.wasm_params), fn.name
        assert fn.wasm_result == "i64", fn.name


def test_arg_names_present() -> None:
    put = functions_by_name["put_contract_data"]
    assert put.arg_names == ("k", "v", "t")


def test_type_table_key_set_matches_observed_vocabulary() -> None:
    """`_model.ENV_TYPE_TO_WASM_TYPE`'s key set must exactly equal the
    arg/return type vocabulary actually observed in the pinned env.json --
    an unrecognized type at re-pin time is a hard failure naming it, never a
    silent `KeyError` deep inside a contract build."""
    doc = json.loads(ENV_JSON.read_text(encoding="utf-8"))
    observed: set[str] = set()
    for module in doc["modules"]:
        for fn in module["functions"]:
            observed.update(arg["type"] for arg in fn["args"])
            observed.add(fn["return"])
    assert set(ENV_TYPE_TO_WASM_TYPE) == observed


def test_functions_without_docs_key_default_to_empty_string() -> None:
    """Six functions in this pin lack the `docs` key entirely."""
    for name in (
        "put_contract_data",
        "has_contract_data",
        "get_contract_data",
        "del_contract_data",
        "compute_hash_sha256",
        "verify_sig_ed25519",
    ):
        assert functions_by_name[name].docs == ""

import pathlib

import pytest
import serpent_host
from stellar_sdk import scval, xdr

_WASM_PATH = pathlib.Path(__file__).parent.parent / "spike1" / "spike.wasm"
if not _WASM_PATH.exists():
    pytest.skip(
        "spike.wasm not built - run: uv run python spikes/spike1/build.py "
        "spikes/spike1/contract_src.py -o spikes/spike1/spike.wasm",
        allow_module_level=True,
    )

WASM = _WASM_PATH.read_bytes()


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


# Everything below this line is beyond the brief's verbatim test.


@pytest.mark.parametrize(
    "bad_name",
    [
        "has-dash",  # character outside [a-zA-Z0-9_]
        "two words",
        "a" * 40,  # exceeds SCSYMBOL_LIMIT (32)
    ],
)
def test_unrepresentable_function_name_raises_catchable_error(bad_name: str) -> None:
    """A bad function name must stay in the normal exception channel.

    Both Symbol::new and TryFromVal<Env, &str> panic on unrepresentable input,
    and a Rust panic reaches Python as PanicException, which subclasses
    BaseException and so slips past `except Exception:`. pytest.raises(RuntimeError)
    would not catch it either, so this test fails loudly on a regression.
    """
    env = serpent_host.RealEnv()
    cid = env.register(WASM)
    with pytest.raises(RuntimeError, match="is not a valid Symbol"):
        env.invoke(cid, bad_name, [])


def test_env_still_usable_after_bad_function_name() -> None:
    """Rejecting a bad name must not poison the host."""
    env = serpent_host.RealEnv()
    cid = env.register(WASM)
    env.invoke(cid, "setup", [scval.to_uint32(1).to_xdr_bytes()])
    with pytest.raises(RuntimeError, match="is not a valid Symbol"):
        env.invoke(cid, "has-dash", [])
    assert _u32(env.invoke(cid, "bump", [])) == 1


def test_missing_function_is_not_reported_as_a_contract_error() -> None:
    """A valid-but-absent name is a host error, not `Error(Contract, #N)`.

    Error::get_code() is 6 here, and Context::InternalError is 7, so mapping
    this arm straight to "contract error code {get_code()}" would let a host
    failure spoof the headline assertion above.
    """
    env = serpent_host.RealEnv()
    cid = env.register(WASM)
    with pytest.raises(RuntimeError) as excinfo:
        env.invoke(cid, "nosuchfn", [])
    assert "contract error code" not in str(excinfo.value)
    assert "host error" in str(excinfo.value)

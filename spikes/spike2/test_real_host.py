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

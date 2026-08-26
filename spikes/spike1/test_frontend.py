import pathlib
import tempfile

from frontend import SpikeCompileError, parse_contract

SPIKE_DIR = pathlib.Path(__file__).parent


def test_parses_target_contract() -> None:
    ir = parse_contract(str(SPIKE_DIR / "contract_src.py"))
    assert ir.name == "Spike"
    assert ir.errors["Error"]["LimitExceeded"] == 7
    assert ("counter_limit", "U32") in ir.structs["Settings"]
    assert [f.name for f in ir.functions] == ["setup", "bump"]
    setup = ir.functions[0]
    assert setup.params == [("counter_limit", "U32")] and setup.ret == "None"


def test_rejects_unsupported_with_location() -> None:
    src = (
        "from serpent_stub import Env, contract\n"   # line 1
        "@contract\n"                                 # line 2
        "class C:\n"                                  # line 3
        "    def f(env: Env) -> None:\n"              # line 4 (annotated: passes the annotation rule)
        "        for i in [1]:\n"                     # line 5 <- must be the reported failure
        "            pass\n"
    )
    p = pathlib.Path(tempfile.mkdtemp()) / "bad.py"
    p.write_text(src)
    try:
        parse_contract(str(p))
        raise AssertionError("should have raised")
    except SpikeCompileError as e:
        assert e.lineno == 5 and "For" in str(e)

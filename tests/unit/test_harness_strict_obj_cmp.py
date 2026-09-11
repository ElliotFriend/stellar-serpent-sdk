"""The mock's `obj_cmp` refuses two non-object words, as the host does (M1-G Task 6, ruling E8).

Until M1-G the mini host accepted `obj_cmp` on two small `Val`s and answered
from tier-1 `val_cmp`, so the shipped small-Symbol compare bug (M1-F B1) was
green at tier 2a and found only by the real host. The deployed shapes bytes
(`tests/real_host/fixtures/testnet/shapes/deployed.wasm`) USED TO carry that
lowering and trapped on chain and on the embedded host, which is what made
them a convenient natural regression fixture for the strict mock at the time.
The M1-end redeploy of 2026-09-11 (`docs/deployments.md`) shipped Task 0's fix
in the deployed bytes too, so that specific artifact no longer trips
`obj_cmp` at all -- `test_two_object_words_still_compare` and
`test_mixed_small_and_object_still_compares` below are what still exercise the
strict check directly, independent of any one deployed contract's history.
"""

from __future__ import annotations

from pathlib import Path

from serpent import U32, Symbol, val
from tests.harness import engine
from tests.harness.hostfns import FullHost

DEPLOYED_SHAPES = (
    Path(__file__).resolve().parents[1]
    / "real_host"
    / "fixtures"
    / "testnet"
    / "shapes"
    / "deployed.wasm"
)


def _drawing(host: FullHost) -> engine.MiniHost:
    mini = engine.MiniHost(DEPLOYED_SHAPES.read_bytes(), imports=host.bindings())
    host.attach(mini)
    assert mini.invoke("draw_rect", val.pack_u32val(5), val.pack_u32val(2)) == val.VOID_VAL
    return mini


def test_the_deployed_shapes_area_no_longer_traps_under_the_default_mock() -> None:
    """B1 retired: the redeployed bytes carry Task 0's fixed lowering, so
    `area` answers under the strict (default) mock exactly as it does under
    the lax one -- see `test_the_lax_mock_still_answers_for_archaeology`."""
    host = FullHost()
    assert host.strict_obj_cmp is True
    mini = _drawing(host)
    word = mini.invoke("area")
    assert word is not None
    assert host.chain_value(word) == U32(10)


def test_the_lax_mock_still_answers_for_archaeology() -> None:
    host = FullHost(strict_obj_cmp=False)
    mini = _drawing(host)
    word = mini.invoke("area")
    assert word is not None
    assert host.chain_value(word) == U32(10)


def test_two_object_words_still_compare() -> None:
    host = FullHost()

    left = host.val_word(Symbol("a_long_symbol_name"))
    right = host.val_word(Symbol("b_long_symbol_name"))
    assert val.is_object(left) and val.is_object(right)
    assert host.obj_cmp(left, right) == val.as_u64(-1)


def test_mixed_small_and_object_still_compares() -> None:
    host = FullHost()

    small = host.val_word(Symbol("abc"))
    obj = host.val_word(Symbol("a_long_symbol_name"))
    assert not val.is_object(small) and val.is_object(obj)
    host.obj_cmp(small, obj)  # answers, does not raise

"""The mock's `obj_cmp` refuses two non-object words, as the host does (M1-G Task 6, ruling E8).

Until M1-G the mini host accepted `obj_cmp` on two small `Val`s and answered
from tier-1 `val_cmp`, so the shipped small-Symbol compare bug (M1-F B1) was
green at tier 2a and found only by the real host. `shapes.py`'s FIRST
deployment (`CDEU7Q4DYJVHL2NENDM263KNXOU73RHHWY2BUWBT2HZX6X4BF4FZ7GNW`,
`docs/deployments.md`'s "shapes (M1-E2 build)" row) carried that lowering and
trapped on chain and on the embedded host running those same bytes -- the
real-artifact regression fixture Task 6 wrote this module against.

Those bytes are no longer the ones committed at
`tests/real_host/fixtures/testnet/shapes/deployed.wasm`: the M1-end redeploy
of 2026-09-11 shipped Task 0's fix on chain too, so that path now answers
`area` correctly under the strict mock as well (the positive control below).
The first deployment's bytes are kept anyway, forever, at
`tests/fixtures/shapes_first_deploy.wasm` (sha256-pinned below, recovered
from history at `6fcf416`) -- that is what keeps Task 6's strict guard
tested against a real compiled artifact rather than only against the
hand-built `Val`s `test_two_object_words_still_compare` and
`test_mixed_small_and_object_still_compares` use: a rewritten or weakened
mock could pass those two and still fail to refuse the actual shape that
shipped the bug.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from serpent import U32, Symbol, val
from tests.harness import engine
from tests.harness.errors import HostTrap
from tests.harness.hostfns import FullHost

DEPLOYED_SHAPES = (
    Path(__file__).resolve().parents[1]
    / "real_host"
    / "fixtures"
    / "testnet"
    / "shapes"
    / "deployed.wasm"
)

#: The FIRST shapes deployment's bytes, recovered from history at `6fcf416`
#: (the last commit before the M1-end redeploy overwrote
#: `tests/real_host/fixtures/testnet/shapes/deployed.wasm` with the fixed
#: bytes): `git show 6fcf416:tests/real_host/fixtures/testnet/shapes/deployed.wasm`.
#: Kept as a permanent fixture so Task 6's strict `obj_cmp` guard has a
#: real-artifact regression test independent of which contract is currently
#: deployed.
FIRST_DEPLOY_SHAPES = Path(__file__).resolve().parents[1] / "fixtures" / "shapes_first_deploy.wasm"

#: Pinned so the artifact cannot silently change under this test: 4,171 bytes,
#: the same sha256 `docs/deployments.md`'s "shapes (M1-E2 build)" row names.
FIRST_DEPLOY_SHAPES_SHA256 = "6a9dd13549bac20f2609ab3d74668963b5249a7943dc7f027cdf6c42bec86e33"


def _drawing(host: FullHost, wasm: Path) -> engine.MiniHost:
    mini = engine.MiniHost(wasm.read_bytes(), imports=host.bindings())
    host.attach(mini)
    assert mini.invoke("draw_rect", val.pack_u32val(5), val.pack_u32val(2)) == val.VOID_VAL
    return mini


def test_the_first_deployments_shapes_area_traps_under_the_default_mock() -> None:
    """Task 6's original regression fixture, restored: the first deployment's
    bytes still carry the B1 lowering, so the strict (default) mock must
    still refuse `area`'s `obj_cmp` on two small symbols, exactly as the real
    host does (and once did on chain)."""
    assert (
        hashlib.sha256(FIRST_DEPLOY_SHAPES.read_bytes()).hexdigest() == FIRST_DEPLOY_SHAPES_SHA256
    )
    host = FullHost()
    assert host.strict_obj_cmp is True
    mini = _drawing(host, FIRST_DEPLOY_SHAPES)
    with pytest.raises(HostTrap, match="two non-object args to obj_cmp"):
        mini.invoke("area")


def test_the_lax_mock_still_answers_for_archaeology() -> None:
    """The SAME first-deployment bytes, under the lax mock: `val_cmp`
    answers instead of refusing, which is what let this bug ship green at
    tier 2a before Task 6 added the strict guard."""
    host = FullHost(strict_obj_cmp=False)
    mini = _drawing(host, FIRST_DEPLOY_SHAPES)
    word = mini.invoke("area")
    assert word is not None
    assert host.chain_value(word) == U32(10)


def test_the_deployed_shapes_area_no_longer_traps_under_the_default_mock() -> None:
    """The positive control: B1 was retired on chain too. The bytes now
    committed at `tests/real_host/fixtures/testnet/shapes/deployed.wasm`
    (the M1-end redeploy) carry Task 0's fixed lowering, so `area` answers
    under the strict (default) mock exactly as it does under the lax one."""
    host = FullHost()
    assert host.strict_obj_cmp is True
    mini = _drawing(host, DEPLOYED_SHAPES)
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

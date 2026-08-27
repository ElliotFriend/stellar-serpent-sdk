import copy
import pickle

import pytest

from serpent import _strkey
from serpent.types import U32, Address, Bytes, Symbol
from serpent.types._ordering import val_cmp

# The same real testnet identifiers as tests/unit/test_strkey.py (Phase 0 evidence).
ACCOUNT = "GCUNZ4XXN2LPHSGWPGCVZAZ4GUWL6HMXLJ7NCHCPB3I23EPY6JCVISSY"
CONTRACT = "CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI"


def test_phase_0_identifiers_round_trip() -> None:
    account = Address(ACCOUNT)
    contract = Address(CONTRACT)
    assert account.strkey == ACCOUNT
    assert contract.strkey == CONTRACT
    assert account.is_account and not account.is_contract
    assert contract.is_contract and not contract.is_account
    assert repr(account) == f"Address({ACCOUNT!r})"


def test_rejects_wrong_kind_and_corrupt_strkeys() -> None:
    # Neither an account (G) nor a contract (C) strkey.
    for wrong_kind in (
        "SBQWY3DNPFWGSZTFNRXWY3DNPFWGSZTFNRXWY3DNPFWGSZTFNRXWY3DNPFWGSZTF",  # S: seed
        "MA7QYNF7SOWQ3GLR2BGMZEHXAVIRZA4KVWLTJJFC7MGXUA74P7UJVAAAAAAAAAAAAAJLK",  # M: muxed
        "hello",
        "",
    ):
        with pytest.raises(ValueError):
            Address(wrong_kind)
    # Right prefix, broken body: the codec's own ValueError surfaces.
    with pytest.raises(ValueError):
        Address(ACCOUNT[:-1] + ("A" if ACCOUNT[-1] != "A" else "B"))  # bad checksum
    with pytest.raises(ValueError):
        Address(CONTRACT[:-1])  # bad length
    with pytest.raises(TypeError):
        Address(b"G")  # type: ignore[arg-type]


def test_accounts_sort_before_contracts_golden() -> None:
    # XDR SCAddressType: SC_ADDRESS_TYPE_ACCOUNT = 0 < SC_ADDRESS_TYPE_CONTRACT = 1,
    # and ScAddress derives Ord on that discriminant -- so every account sorts
    # before every contract, whatever the 32 raw bytes say.
    account = Address(ACCOUNT)
    contract = Address(CONTRACT)
    assert val_cmp(account, contract) < 0
    assert val_cmp(contract, account) > 0
    assert account < contract and contract > account
    assert sorted([contract, account]) == [account, contract]
    # These two Phase 0 identifiers happen to agree with their raw-byte order,
    # so they pin the golden but do not by themselves prove the discriminant is
    # doing the work. This pair does: the account's raw bytes are the largest
    # possible and the contract's the smallest, and the account still sorts first.
    raw_account = _strkey.decode(_strkey.VERSION_ACCOUNT, ACCOUNT)
    raw_contract = _strkey.decode(_strkey.VERSION_CONTRACT, CONTRACT)
    assert raw_account < raw_contract
    max_account = Address(_strkey.encode(_strkey.VERSION_ACCOUNT, b"\xff" * 32))
    min_contract = Address(_strkey.encode(_strkey.VERSION_CONTRACT, b"\x00" * 32))
    assert val_cmp(max_account, min_contract) < 0
    assert sorted([min_contract, max_account]) == [max_account, min_contract]


def test_ordering_within_a_kind_is_by_raw_bytes() -> None:
    low = Address(_strkey.encode(_strkey.VERSION_ACCOUNT, b"\x00" * 32))
    high = Address(_strkey.encode(_strkey.VERSION_ACCOUNT, b"\xff" * 32))
    contract_low = Address(_strkey.encode(_strkey.VERSION_CONTRACT, b"\x00" * 32))
    assert low < high and val_cmp(low, high) < 0
    assert high < contract_low  # kind still wins over the payload
    assert val_cmp(low, low) == 0


def test_cmp_payload_is_the_discriminant_then_raw_bytes() -> None:
    raw = _strkey.decode(_strkey.VERSION_ACCOUNT, ACCOUNT)
    assert Address(ACCOUNT)._cmp_payload() == b"\x00" + raw
    raw_c = _strkey.decode(_strkey.VERSION_CONTRACT, CONTRACT)
    assert Address(CONTRACT)._cmp_payload() == b"\x01" + raw_c
    assert Address._SCVAL_RANK == 18


def test_equality_never_raises_and_hash_is_consistent() -> None:
    not_a_chain_value: object = None
    assert Address(ACCOUNT) == Address(ACCOUNT)
    assert (Address(ACCOUNT) == Address(CONTRACT)) is False
    assert (Address(ACCOUNT) == ACCOUNT) is False  # no coercion from the strkey str
    assert (Address(ACCOUNT) == Symbol("a")) is False
    assert (Address(ACCOUNT) == U32(1)) is False
    assert (Address(ACCOUNT) == Bytes(b"a")) is False
    assert (Address(ACCOUNT) == not_a_chain_value) is False
    assert hash(Address(ACCOUNT)) == hash(Address(ACCOUNT))
    assert len({Address(ACCOUNT), Address(ACCOUNT), Address(CONTRACT)}) == 2


def test_ordering_against_other_chain_types_raises() -> None:
    with pytest.raises(TypeError):
        _ = Address(ACCOUNT) < Symbol("a")  # type: ignore[operator]
    with pytest.raises(TypeError):
        _ = Address(ACCOUNT) < Bytes(b"a")  # type: ignore[operator]
    with pytest.raises(TypeError):
        _ = Address(ACCOUNT) <= U32(1)  # type: ignore[operator]
    with pytest.raises(TypeError):
        _ = Address(ACCOUNT) > ACCOUNT  # type: ignore[operator]


def test_val_cmp_places_address_after_every_other_type() -> None:
    for other in (U32(1), Bytes(b"\xff"), Symbol("zzzzz")):
        assert val_cmp(other, Address(ACCOUNT)) < 0
        assert val_cmp(Address(ACCOUNT), other) > 0


def test_immutability_and_copy_round_trips() -> None:
    address = Address(ACCOUNT)
    with pytest.raises(AttributeError):
        address._payload = CONTRACT
    for clone in (
        copy.copy(address),
        copy.deepcopy(address),
        pickle.loads(pickle.dumps(address)),
    ):
        assert type(clone) is Address
        assert clone == address and clone.strkey == ACCOUNT
        assert clone.is_account


def test_val_forms_await_sub_plan_b() -> None:
    with pytest.raises(NotImplementedError, match="sub-plan B"):
        Address(ACCOUNT).to_val()
    with pytest.raises(NotImplementedError, match="sub-plan B"):
        Address.from_val(0)


def test_require_auth_awaits_the_env_runtime() -> None:
    from serpent.types import Vec

    address = Address(ACCOUNT)
    with pytest.raises(NotImplementedError, match="sub-plan E"):
        address.require_auth()
    with pytest.raises(NotImplementedError, match="sub-plan E"):
        address.require_auth_for_args(Vec(U32, [U32(1)]))

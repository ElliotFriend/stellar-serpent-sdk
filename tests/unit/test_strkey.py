import pytest

from serpent import _strkey

# Real identifiers from this repo's Phase 0 evidence (testnet):
ACCOUNT = "GCUNZ4XXN2LPHSGWPGCVZAZ4GUWL6HMXLJ7NCHCPB3I23EPY6JCVISSY"
CONTRACT = "CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI"


def test_round_trip_account_and_contract() -> None:
    raw_g = _strkey.decode(_strkey.VERSION_ACCOUNT, ACCOUNT)
    assert len(raw_g) == 32
    assert _strkey.encode(_strkey.VERSION_ACCOUNT, raw_g) == ACCOUNT
    raw_c = _strkey.decode(_strkey.VERSION_CONTRACT, CONTRACT)
    assert len(raw_c) == 32
    assert _strkey.encode(_strkey.VERSION_CONTRACT, raw_c) == CONTRACT


def test_wrong_version_rejected() -> None:
    with pytest.raises(ValueError):
        _strkey.decode(_strkey.VERSION_ACCOUNT, CONTRACT)


def test_corrupt_checksum_rejected() -> None:
    bad = ACCOUNT[:-1] + ("A" if ACCOUNT[-1] != "A" else "B")
    with pytest.raises(ValueError):
        _strkey.decode(_strkey.VERSION_ACCOUNT, bad)


def test_matches_stellar_sdk() -> None:
    from stellar_sdk import StrKey

    assert _strkey.decode(_strkey.VERSION_ACCOUNT, ACCOUNT) == StrKey.decode_ed25519_public_key(
        ACCOUNT
    )
    assert _strkey.decode(_strkey.VERSION_CONTRACT, CONTRACT) == StrKey.decode_contract(CONTRACT)


# --- Additional example-based coverage for decode's other reject cases -----
# (bad checksum and wrong version are covered above; length and charset/
# padding are required by the algorithm spec but not exercised by the
# brief's Step 1 block, so covered here.)


def test_wrong_length_rejected() -> None:
    with pytest.raises(ValueError):
        _strkey.decode(_strkey.VERSION_ACCOUNT, ACCOUNT + "A")
    with pytest.raises(ValueError):
        _strkey.decode(_strkey.VERSION_ACCOUNT, ACCOUNT[:-1])


def test_invalid_charset_or_padding_rejected() -> None:
    with pytest.raises(ValueError):
        _strkey.decode(_strkey.VERSION_ACCOUNT, "0" + ACCOUNT[1:])  # '0'/'1' aren't RFC 4648 base32
    with pytest.raises(ValueError):
        _strkey.decode(_strkey.VERSION_ACCOUNT, ACCOUNT[:-1] + "=")


# --- Hypothesis: round trips over random payloads + differential vs. stellar_sdk

from hypothesis import given
from hypothesis import strategies as st

payloads = st.binary(min_size=32, max_size=32)


@given(payloads)
def test_round_trip_property_account(payload: bytes) -> None:
    encoded = _strkey.encode(_strkey.VERSION_ACCOUNT, payload)
    assert _strkey.decode(_strkey.VERSION_ACCOUNT, encoded) == payload


@given(payloads)
def test_round_trip_property_contract(payload: bytes) -> None:
    encoded = _strkey.encode(_strkey.VERSION_CONTRACT, payload)
    assert _strkey.decode(_strkey.VERSION_CONTRACT, encoded) == payload


@given(payloads)
def test_encode_matches_stellar_sdk_differential(payload: bytes) -> None:
    from stellar_sdk import StrKey

    assert _strkey.encode(_strkey.VERSION_ACCOUNT, payload) == StrKey.encode_ed25519_public_key(
        payload
    )
    assert _strkey.encode(_strkey.VERSION_CONTRACT, payload) == StrKey.encode_contract(payload)

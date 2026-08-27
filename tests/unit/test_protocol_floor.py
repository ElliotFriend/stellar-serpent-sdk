import pytest

from serpent._host import (
    BASE_PROTOCOL,
    DEFAULT_TARGET_PROTOCOL,
    ProtocolGateError,
    check_protocol_target,
    compute_protocol_floor,
    declared_protocol,
)

# The eight host functions Phase 0 exercised against the live network; none
# of them carries a min_supported_protocol in v28.0.2.
PHASE0_FNS = {
    "put_contract_data",
    "has_contract_data",
    "get_contract_data",
    "map_new_from_linear_memory",
    "map_get",
    "symbol_new_from_linear_memory",
    "string_new_from_linear_memory",
    "fail_with_error",
}


def test_constants() -> None:
    assert BASE_PROTOCOL == 20
    assert DEFAULT_TARGET_PROTOCOL == 27


def test_floor_of_ungated_functions_is_base_protocol() -> None:
    assert compute_protocol_floor(PHASE0_FNS) == BASE_PROTOCOL


def test_floor_rises_to_a_gated_functions_min_protocol() -> None:
    assert compute_protocol_floor(PHASE0_FNS | {"extend_contract_data_ttl_v2"}) == 26
    assert compute_protocol_floor(PHASE0_FNS | {"delegate_account_auth"}) == 27


def test_floor_unknown_name_raises_key_error_naming_it() -> None:
    with pytest.raises(KeyError, match="not_a_real_host_fn"):
        compute_protocol_floor(["not_a_real_host_fn"])


def test_check_protocol_target_passes_for_functions_within_target() -> None:
    check_protocol_target(PHASE0_FNS, DEFAULT_TARGET_PROTOCOL)


def test_check_protocol_target_rejects_a_fn_whose_min_protocol_exceeds_target() -> None:
    with pytest.raises(ProtocolGateError, match="sparse_map_new_from_linear_memory"):
        check_protocol_target(
            PHASE0_FNS | {"sparse_map_new_from_linear_memory"}, DEFAULT_TARGET_PROTOCOL
        )


def test_check_protocol_target_rejects_a_fn_whose_max_protocol_is_below_target() -> None:
    with pytest.raises(ProtocolGateError, match="protocol_gated_dummy"):
        check_protocol_target(PHASE0_FNS | {"protocol_gated_dummy"}, DEFAULT_TARGET_PROTOCOL)


def test_check_protocol_target_names_every_offender() -> None:
    fn_names = PHASE0_FNS | {"sparse_map_new_from_linear_memory", "protocol_gated_dummy"}
    with pytest.raises(ProtocolGateError) as exc_info:
        check_protocol_target(fn_names, DEFAULT_TARGET_PROTOCOL)
    message = str(exc_info.value)
    assert "sparse_map_new_from_linear_memory" in message
    assert "protocol_gated_dummy" in message


def test_check_protocol_target_dedupes_a_repeated_offender() -> None:
    """A duplicate input name must be named only once in the error message,
    not once per occurrence."""
    with pytest.raises(ProtocolGateError) as exc_info:
        check_protocol_target(PHASE0_FNS | {"protocol_gated_dummy"}, DEFAULT_TARGET_PROTOCOL)
    baseline_count = str(exc_info.value).count("protocol_gated_dummy")

    with pytest.raises(ProtocolGateError) as exc_info_dup:
        check_protocol_target(
            [*PHASE0_FNS, "protocol_gated_dummy", "protocol_gated_dummy"],
            DEFAULT_TARGET_PROTOCOL,
        )
    assert str(exc_info_dup.value).count("protocol_gated_dummy") == baseline_count


def test_check_protocol_target_unknown_name_raises_key_error() -> None:
    with pytest.raises(KeyError, match="not_a_real_host_fn"):
        check_protocol_target(["not_a_real_host_fn"], DEFAULT_TARGET_PROTOCOL)


def test_declared_protocol_defaults_to_the_computed_floor_not_the_ceiling() -> None:
    assert declared_protocol(PHASE0_FNS, None) == BASE_PROTOCOL == 20
    assert declared_protocol(PHASE0_FNS | {"delegate_account_auth"}, None) == 27


def test_declared_protocol_accepts_an_explicit_value_at_or_above_the_floor() -> None:
    assert declared_protocol(PHASE0_FNS, BASE_PROTOCOL) == BASE_PROTOCOL
    assert declared_protocol(PHASE0_FNS, DEFAULT_TARGET_PROTOCOL) == DEFAULT_TARGET_PROTOCOL


def test_declared_protocol_gate_error_takes_precedence_over_floor_check() -> None:
    """`requested=20` is also below `delegate_account_auth`'s floor of 27, but
    `check_protocol_target` runs first and fires on the gate violation --
    this must raise `ProtocolGateError` specifically, not the floor
    `ValueError` (the two failure modes are easy to conflate: both are
    `ValueError`s and this message happens to contain "27" too)."""
    with pytest.raises(ProtocolGateError, match="delegate_account_auth"):
        declared_protocol(PHASE0_FNS | {"delegate_account_auth"}, 20)


def test_declared_protocol_raises_value_error_when_requested_is_below_the_floor() -> None:
    """A genuinely gate-clean function set (PHASE0_FNS, floor 20) with a
    `requested` below that floor must hit `_protocol.py`'s `requested <
    floor` branch, not `check_protocol_target`."""
    with pytest.raises(ValueError, match="below the computed floor 20") as exc_info:
        declared_protocol(PHASE0_FNS, 10)
    assert not isinstance(exc_info.value, ProtocolGateError)


def test_declared_protocol_raises_protocol_gate_error_for_an_incompatible_function() -> None:
    with pytest.raises(ProtocolGateError, match="protocol_gated_dummy"):
        declared_protocol(PHASE0_FNS | {"protocol_gated_dummy"}, DEFAULT_TARGET_PROTOCOL)


def test_declared_protocol_unknown_name_raises_key_error() -> None:
    with pytest.raises(KeyError, match="not_a_real_host_fn"):
        declared_protocol(["not_a_real_host_fn"], None)


def test_protocol_gate_error_is_a_value_error() -> None:
    assert issubclass(ProtocolGateError, ValueError)

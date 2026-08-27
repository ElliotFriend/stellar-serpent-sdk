"""Value tables for the raw-scalar host-function arguments.

Both enums below are marshalled as a raw `u64`, never as an encoded `Val`
(see `_model.RAW_SCALAR_TYPES`).
"""

#: `StorageType` -- the `t` argument to `put_contract_data` et al.
#: Source: spec Sec.13 / rs-soroban-env v28.0.2,
#: soroban-env-common/src/storage_type.rs:
#: `pub enum StorageType { Temporary = 0, Persistent = 1, Instance = 2 }`
#: https://github.com/stellar/rs-soroban-env/blob/v28.0.2/soroban-env-common/src/storage_type.rs
STORAGE_TYPE: dict[str, int] = {
    "temporary": 0,
    "persistent": 1,
    "instance": 2,
}

#: `ContractTtlExtension` -- selects which entries
#: `extend_contract_instance_and_code_ttl_v2` extends.
#: Source: rs-soroban-env v28.0.2 (tag verified against
#: `git rev-parse refs/tags/v28.0.2` = 5061e9c40ce6fc74ade9b9b3b49465b0cf7fdccb),
#: soroban-env-common/src/storage_type.rs:
#: `pub enum ContractTtlExtension { InstanceAndCode = 0, Instance = 1, Code = 2 }`
#: https://github.com/stellar/rs-soroban-env/blob/v28.0.2/soroban-env-common/src/storage_type.rs
CONTRACT_TTL_EXTENSION: dict[str, int] = {
    "instance_and_code": 0,
    "instance": 1,
    "code": 2,
}

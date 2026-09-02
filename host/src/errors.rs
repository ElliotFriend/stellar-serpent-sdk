//! One classification for every `soroban_sdk::Error` (P4): the TYPE is tested
//! with `is_type` for each `ScErrorType` variant (`get_type` deliberately
//! does not exist upstream), the code with `get_code`. A `Context(InvalidAction)`
//! must never be reported as contract code 6, and `InternalError = 7` must
//! never impersonate a contract's code 7.

use soroban_sdk::xdr::{ScErrorCode, ScErrorType};
use soroban_sdk::Error;

pub const ALL_TYPES: [(ScErrorType, &str); 10] = [
    (ScErrorType::Contract, "Contract"),
    (ScErrorType::WasmVm, "WasmVm"),
    (ScErrorType::Context, "Context"),
    (ScErrorType::Storage, "Storage"),
    (ScErrorType::Object, "Object"),
    (ScErrorType::Crypto, "Crypto"),
    (ScErrorType::Events, "Events"),
    (ScErrorType::Budget, "Budget"),
    (ScErrorType::Value, "Value"),
    (ScErrorType::Auth, "Auth"),
];

pub struct Classified {
    pub is_contract: bool,
    pub type_name: &'static str,
    pub code: u32,
    pub message: String,
}

pub fn classify(e: Error) -> Classified {
    let (type_name, is_contract) = ALL_TYPES
        .iter()
        .find(|(ty, _)| e.is_type(*ty))
        .map(|(ty, name)| (*name, *ty == ScErrorType::Contract))
        .unwrap_or(("Unknown", false));
    let code = e.get_code();
    let message = if is_contract {
        format!("contract error code {code}")
    } else {
        let code_name = ScErrorCode::try_from(code as i32)
            .map(|c| format!("{c:?}"))
            .unwrap_or_else(|_| code.to_string());
        format!("host error Error({type_name}, {code_name})")
    };
    Classified {
        is_contract,
        type_name,
        code,
        message,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_contract_error_is_contract_with_its_code() {
        let c = classify(Error::from_contract_error(7));
        assert!(c.is_contract);
        assert_eq!(c.type_name, "Contract");
        assert_eq!(c.code, 7);
        assert_eq!(c.message, "contract error code 7");
    }

    #[test]
    fn internal_error_seven_is_not_a_contract_error() {
        // The P4 spoof: ScErrorCode::InternalError == 7.
        let c = classify(Error::from_type_and_code(
            ScErrorType::Context,
            ScErrorCode::InternalError,
        ));
        assert!(!c.is_contract);
        assert_eq!(c.type_name, "Context");
        assert_eq!(c.code, ScErrorCode::InternalError as u32);
        assert!(c
            .message
            .starts_with("host error Error(Context, InternalError)"));
    }

    #[test]
    fn every_type_is_named() {
        for (ty, name) in ALL_TYPES {
            let c = classify(Error::from_type_and_code(ty, ScErrorCode::InvalidAction));
            assert_eq!(c.type_name, name);
        }
    }
}

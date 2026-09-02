//! Boundary pre-validation (P3): nothing crosses into soroban-sdk that could
//! make it panic. "Returns Result" is an unverified claim in this SDK.

pub const SCSYMBOL_LIMIT: usize = 32;

pub fn check_symbol(text: &str) -> Result<(), String> {
    if text.len() > SCSYMBOL_LIMIT {
        return Err(format!(
            "{text:?} is not a valid Symbol: {} bytes exceeds the {SCSYMBOL_LIMIT}-byte limit",
            text.len()
        ));
    }
    if let Some(bad) = text
        .chars()
        .find(|c| !c.is_ascii_alphanumeric() && *c != '_')
    {
        return Err(format!(
            "{text:?} is not a valid Symbol: character {bad:?} is outside [a-zA-Z0-9_]"
        ));
    }
    Ok(())
}

/// A contract strkey: 56 chars, base32 alphabet, leading 'C'. The full
/// checksum is verified by `stellar_strkey::Contract::from_string`, which
/// returns `Result` (verified panic-free at 0.0.16 by the Task 1 unit test).
pub fn check_contract_strkey(text: &str) -> Result<(), String> {
    const ALPHABET: &str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
    if text.len() != 56 || !text.starts_with('C') {
        return Err(format!(
            "{text:?} is not a contract strkey (56 chars, leading 'C')"
        ));
    }
    if let Some(bad) = text.chars().find(|c| !ALPHABET.contains(*c)) {
        return Err(format!(
            "{text:?} is not a contract strkey: character {bad:?} is not base32"
        ));
    }
    // Full checksum verification through a Result-returning parser (never a panic).
    stellar_strkey::Contract::from_string(text)
        .map(|_| ())
        .map_err(|e| format!("{text:?} is not a contract strkey: {e:?}"))
}

/// The wasm magic + version; the host's own validator does the rest and
/// reports through `HostError`, not a panic (verified by Task 1's Python test
/// `test_register_of_garbage_is_a_host_failure_not_a_panic`).
pub fn check_wasm_header(bytes: &[u8]) -> Result<(), String> {
    if bytes.len() < 8 || &bytes[0..4] != b"\0asm" || bytes[4..8] != [1, 0, 0, 0] {
        return Err("not a wasm module: bad magic or version header".to_string());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn symbol_limits_and_charset() {
        assert!(check_symbol("bump").is_ok());
        assert!(check_symbol("a".repeat(32).as_str()).is_ok());
        assert!(check_symbol("a".repeat(33).as_str()).is_err());
        assert!(check_symbol("has-dash").is_err());
        assert!(check_symbol("two words").is_err());
        assert!(
            check_symbol("").is_ok(),
            "the host accepts the empty symbol; the frontend never emits it"
        );
    }

    #[test]
    fn strkey_shape() {
        assert!(
            check_contract_strkey("CDEU7Q4DYJVHL2NENDM263KNXOU73RHHWY2BUWBT2HZX6X4BF4FZ7GNW")
                .is_ok()
        );
        assert!(check_contract_strkey("NOTANADDRESS").is_err());
        assert!(
            check_contract_strkey("GCUNZ4XXN2LPHSGWPGCVZAZ4GUWL6HMXLJ7NCHCPB3I23EPY6JCVISSY")
                .is_err()
        );
        let mut bad = String::from("CDEU7Q4DYJVHL2NENDM263KNXOU73RHHWY2BUWBT2HZX6X4BF4FZ7GN");
        bad.push('0'); // '0' is not base32
        assert!(check_contract_strkey(&bad).is_err());
    }

    #[test]
    fn wasm_header() {
        assert!(check_wasm_header(b"\0asm\x01\0\0\0").is_ok());
        assert!(check_wasm_header(b"hello").is_err());
        assert!(check_wasm_header(b"\0asm\x02\0\0\0").is_err());
    }
}

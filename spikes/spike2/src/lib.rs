//! Spike 2: embed the real `soroban-env-host` (via soroban-sdk testutils) as a
//! Python extension module, so the same contract bytes that ran on testnet can
//! be executed in-process from Python.

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use soroban_sdk::testutils::{EnvTestConfig, Ledger as _};
use soroban_sdk::xdr::{Limits, ReadXdr, ScErrorType, ScVal, WriteXdr, SCSYMBOL_LIMIT};
use soroban_sdk::{Address, Env, Symbol, TryFromVal, TryIntoVal, Val, Vec as SorobanVec};

/// Build a `Symbol` from an arbitrary Python-supplied string without panicking.
///
/// Neither `Symbol::new` nor the nominally-fallible `TryFromVal<Env, &str>`
/// impl is actually panic-free on soroban-sdk 27.0.6: the latter's error type
/// is `ConversionError`, but its body reaches
/// `soroban-sdk/src/unwrap.rs:46` (`unwrap_optimized` -> plain `unwrap()`) and
/// panics with `HostError: Error(Value, InvalidInput)` on bad input. A panic
/// crosses into Python as `pyo3_runtime.PanicException`, which subclasses
/// `BaseException` and so escapes `except Exception:`. So validate first,
/// against the SDK's own rules: at most `SCSYMBOL_LIMIT` (32) bytes, drawn from
/// `[a-zA-Z0-9_]`.
fn symbol_from_str(env: &Env, func: &str) -> PyResult<Symbol> {
    if func.len() > SCSYMBOL_LIMIT as usize {
        return Err(PyRuntimeError::new_err(format!(
            "function name {func:?} is not a valid Symbol: {} bytes exceeds the {SCSYMBOL_LIMIT}-byte limit",
            func.len()
        )));
    }
    if let Some(bad) = func
        .chars()
        .find(|c| !c.is_ascii_alphanumeric() && *c != '_')
    {
        return Err(PyRuntimeError::new_err(format!(
            "function name {func:?} is not a valid Symbol: character {bad:?} is outside [a-zA-Z0-9_]"
        )));
    }
    Symbol::try_from_val(env, &func).map_err(|e| {
        PyRuntimeError::new_err(format!(
            "function name {func:?} is not a valid Symbol: {e:?}"
        ))
    })
}

/// `soroban_sdk::Env` is `Rc`-backed and therefore neither `Send` nor `Sync`,
/// so the pyclass has to be `unsendable` (pyo3 then panics if Python touches it
/// from another thread instead of failing to compile).
#[pyclass(unsendable)]
struct RealEnv {
    env: Env,
}

#[pymethods]
impl RealEnv {
    #[new]
    fn new() -> Self {
        // Without `capture_snapshot_at_drop: false` the sdk's Drop impl writes
        // test_snapshots/*.json into the process CWD whenever the thread has a
        // name (which it does under pytest).
        let env = Env::new_with_config(EnvTestConfig {
            capture_snapshot_at_drop: false,
            ..Default::default()
        });
        env.mock_all_auths();
        RealEnv { env }
    }

    /// Upload + instantiate the wasm, returning the contract's strkey address.
    fn register(&self, wasm: &[u8]) -> PyResult<String> {
        let addr = self.env.register(wasm, ());
        Ok(addr.to_string().to_string())
    }

    /// Invoke `func` on `contract` with ScVal-XDR arguments, returning the
    /// ScVal-XDR result. Contract errors surface as `RuntimeError`.
    fn invoke(&self, contract: &str, func: &str, args_xdr: Vec<Vec<u8>>) -> PyResult<Vec<u8>> {
        let env = &self.env;

        let addr_string = soroban_sdk::String::from_str(env, contract);
        let addr = Address::from_string(&addr_string);
        let sym = symbol_from_str(env, func)?;

        let mut args: SorobanVec<Val> = SorobanVec::new(env);
        for (i, bytes) in args_xdr.iter().enumerate() {
            let scval = ScVal::from_xdr(bytes, Limits::none()).map_err(|e| {
                PyRuntimeError::new_err(format!("arg {i}: not valid ScVal XDR: {e:?}"))
            })?;
            let val: Val = scval.try_into_val(env).map_err(|e| {
                PyRuntimeError::new_err(format!("arg {i}: ScVal -> Val failed: {e:?}"))
            })?;
            args.push_back(val);
        }

        match env.try_invoke_contract::<Val, soroban_sdk::Error>(&addr, &sym, args) {
            Ok(Ok(val)) => {
                let scval = ScVal::try_from_val(env, &val).map_err(|e| {
                    PyRuntimeError::new_err(format!("result: Val -> ScVal failed: {e:?}"))
                })?;
                scval.to_xdr(Limits::none()).map_err(|e| {
                    PyRuntimeError::new_err(format!("result: ScVal -> XDR failed: {e:?}"))
                })
            }
            // Unreachable for T = Val (its TryFromVal error is Infallible), but
            // the signature forces us to name the arm.
            Ok(Err(e)) => Err(PyRuntimeError::new_err(format!(
                "result conversion failed: {e:?}"
            ))),
            // NOTE: with E = soroban_sdk::Error this arm catches *every* error
            // the host can express, not just Error(Contract, #N) -- calling a
            // missing function lands here as Error(Context, InvalidAction),
            // whose get_code() is 6. Reporting that as "contract error code 6"
            // would be a lie, so discriminate on the type first. `Error`
            // intentionally exposes no `get_type()` (it would have to cast a
            // possibly-bad bit pattern into an ScErrorType); `is_type` is the
            // supported test, and Debug renders as `Error(Context, ...)`.
            Err(Ok(e)) => {
                let msg = if e.is_type(ScErrorType::Contract) {
                    format!("contract error code {}", e.get_code())
                } else {
                    format!("host error {e:?}")
                };
                Err(PyRuntimeError::new_err(msg))
            }
            Err(Err(invoke_err)) => Err(PyRuntimeError::new_err(format!("{invoke_err:?}"))),
        }
    }

    fn set_ledger(&self, timestamp: u64, sequence: u32, protocol: u32) -> PyResult<()> {
        self.env.ledger().with_mut(|l| {
            l.timestamp = timestamp;
            l.sequence_number = sequence;
            l.protocol_version = protocol;
        });
        Ok(())
    }
}

#[pymodule]
fn serpent_host(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RealEnv>()
}

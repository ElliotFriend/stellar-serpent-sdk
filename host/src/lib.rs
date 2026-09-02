//! serpent's tier-2b host (dossier §D.1): ONE `#[pyclass(unsendable)]` over
//! the soroban-sdk test `Env`, every method ScVal-XDR bytes in and out, every
//! method wrapped in `catch_unwind`, every failure one `HostFailure`. Rust
//! knows nothing about serpent types; `serpent.testing` (Python) does.

pub mod errors;
pub mod validate;

use std::panic::{catch_unwind, AssertUnwindSafe};

use pyo3::create_exception;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};
use soroban_sdk::testutils::storage::{Instance as _, Persistent as _, Temporary as _};
// No `testutils::Address` trait import: `Address::from_string` is inherent on
// the sdk type, and the trait (which only adds `generate`) would be an unused
// import under `-D warnings`.
use soroban_sdk::testutils::{
    AuthorizedFunction, EnvTestConfig, Events as _, Ledger as _, LedgerInfo, MockAuth,
    MockAuthInvoke,
};
use soroban_sdk::xdr::{DiagnosticEvent, Limits, ReadXdr, ScVal, WriteXdr};
use soroban_sdk::{Address, Env, Symbol, TryFromVal, TryIntoVal, Val, Vec as SorobanVec};

create_exception!(
    serpent_host,
    HostFailure,
    PyException,
    "args == (kind, error_type, code, message); see serpent.testing._errors"
);

fn failure(kind: &str, error_type: &str, code: u32, message: String) -> PyErr {
    HostFailure::new_err((kind.to_string(), error_type.to_string(), code, message))
}

fn invalid(message: String) -> PyErr {
    failure("invalid_input", "", 0, message)
}

fn conversion(message: String) -> PyErr {
    failure("conversion", "", 0, message)
}

/// E4: a residual panic anywhere below becomes a catchable `HostFailure`
/// of kind "panic", never a `pyo3_runtime.PanicException` (P3).
fn contained<T>(f: impl FnOnce() -> PyResult<T>) -> PyResult<T> {
    match catch_unwind(AssertUnwindSafe(f)) {
        Ok(result) => result,
        Err(payload) => {
            let text = payload
                .downcast_ref::<String>()
                .cloned()
                .or_else(|| payload.downcast_ref::<&str>().map(|s| s.to_string()))
                .unwrap_or_else(|| "non-string panic payload".to_string());
            Err(failure(
                "panic",
                "",
                0,
                format!("the embedded host panicked: {text}"),
            ))
        }
    }
}

/// The one place a panic is an ANSWER rather than a failure: the testutils
/// `get_ttl` panics on an absent or expired entry (review B10), and "no live
/// entry" is `None` in this module's contract.
fn unwind_to_none<T>(f: impl FnOnce() -> T) -> Option<T> {
    catch_unwind(AssertUnwindSafe(f)).ok()
}

fn scval_from(bytes: &[u8], what: &str) -> PyResult<ScVal> {
    ScVal::from_xdr(bytes, Limits::none())
        .map_err(|e| invalid(format!("{what}: not ScVal XDR: {e:?}")))
}

fn xdr_bytes<'py>(py: Python<'py>, v: &ScVal) -> PyResult<Bound<'py, PyBytes>> {
    let bytes = v
        .to_xdr(Limits::none())
        .map_err(|e| conversion(format!("ScVal -> XDR: {e:?}")))?;
    Ok(PyBytes::new(py, &bytes))
}

fn to_val(env: &Env, bytes: &[u8], what: &str) -> PyResult<Val> {
    let scval = scval_from(bytes, what)?;
    scval
        .try_into_val(env)
        .map_err(|e| conversion(format!("{what}: ScVal -> Val: {e:?}")))
}

fn address_of(env: &Env, strkey: &str) -> PyResult<Address> {
    validate::check_contract_strkey(strkey).map_err(invalid)?;
    Ok(Address::from_string(&soroban_sdk::String::from_str(
        env, strkey,
    )))
}

fn symbol_of(env: &Env, name: &str) -> PyResult<Symbol> {
    validate::check_symbol(name).map_err(invalid)?;
    Symbol::try_from_val(env, &name).map_err(|e| invalid(format!("{name:?}: {e:?}")))
}

/// The three storage maps a contract can address. Parsed BEFORE anything else
/// touches the sdk, so a typo is `invalid_input` and never a frame push.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Durability {
    Persistent,
    Temporary,
    Instance,
}

/// One recorded authorization as Python sees it: `(address, contract,
/// function, args ScVal XDR)`. A named alias because the tuple-in-a-Vec is
/// past clippy's `type_complexity` threshold inline.
type AuthRow<'py> = (String, String, String, Vec<Bound<'py, PyBytes>>);

fn durability_of(name: &str) -> PyResult<Durability> {
    match name {
        "persistent" => Ok(Durability::Persistent),
        "temporary" => Ok(Durability::Temporary),
        "instance" => Ok(Durability::Instance),
        other => Err(invalid(format!(
            "durability {other:?} is not one of persistent/temporary/instance"
        ))),
    }
}

#[pyclass(unsendable)]
struct RealEnv {
    env: Env,
    invoked: std::cell::Cell<bool>,
}

#[pymethods]
impl RealEnv {
    #[new]
    #[pyo3(signature = (*, protocol_version, sequence_number, timestamp, network_id, base_reserve,
                        min_temp_entry_ttl, min_persistent_entry_ttl, max_entry_ttl))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        protocol_version: u32,
        sequence_number: u32,
        timestamp: u64,
        network_id: &[u8],
        base_reserve: u32,
        min_temp_entry_ttl: u32,
        min_persistent_entry_ttl: u32,
        max_entry_ttl: u32,
    ) -> PyResult<Self> {
        contained(|| {
            let id: [u8; 32] = network_id
                .try_into()
                .map_err(|_| invalid("network_id must be exactly 32 bytes".to_string()))?;
            // P11: without this the sdk's Drop writes test_snapshots/*.json into the CWD.
            let env = Env::new_with_config(EnvTestConfig {
                capture_snapshot_at_drop: false,
            });
            env.ledger().set(LedgerInfo {
                protocol_version,
                sequence_number,
                timestamp,
                network_id: id,
                base_reserve,
                min_temp_entry_ttl,
                min_persistent_entry_ttl,
                max_entry_ttl,
            });
            Ok(RealEnv {
                env,
                invoked: std::cell::Cell::new(false),
            })
        })
    }

    /// `Ledger::protocol_version()` is `#[deprecated]` and fails `-D warnings`
    /// (review B6); the `LedgerInfo` read is the supported form.
    fn protocol_version(&self) -> PyResult<u32> {
        contained(|| Ok(self.env.ledger().get().protocol_version))
    }

    /// The compiled-in ceiling (P10), from the env-host crate directly (the
    /// sdk's `env::internal` is private, review B6).
    fn host_protocol_ceiling(&self) -> PyResult<u32> {
        contained(|| Ok(soroban_env_host::Host::current_test_protocol()))
    }

    /// The LAST invocation's diagnostic events as XDR (review B5): the innermost
    /// `topics: [error, Error(Type(Code))]` is the real classification the frame-
    /// level error hides behind `Context(InvalidAction)`.
    fn diagnostics<'py>(&self, py: Python<'py>) -> PyResult<Vec<Bound<'py, PyBytes>>> {
        contained(|| {
            let host: &soroban_env_host::Host = self.env.host();
            let events = host
                .get_diagnostic_events()
                .map_err(|e| conversion(format!("diagnostics: {e:?}")))?;
            events
                .0
                .iter()
                .map(|ev| {
                    // A `DiagnosticEvent`, not a bare `ContractEvent`: the
                    // `failed_call` flag is the rollback evidence one layer
                    // below `Events::all()` (review m7), and Task 3 decodes
                    // these with `xdr.DiagnosticEvent.from_xdr_bytes`.
                    let diagnostic = DiagnosticEvent {
                        in_successful_contract_call: !ev.failed_call,
                        event: ev.event.clone(),
                    };
                    let b = diagnostic
                        .to_xdr(Limits::none())
                        .map_err(|e| conversion(format!("diagnostic event: {e:?}")))?;
                    Ok(PyBytes::new(py, &b))
                })
                .collect()
        })
    }

    /// The host's own `Compare<Val>` verdict for any two Vals (review M2) --
    /// `obj_cmp` refuses two small operands, this does not.
    fn compare(&self, a_xdr: &[u8], b_xdr: &[u8]) -> PyResult<i32> {
        contained(|| {
            use soroban_env_host::Compare;
            let a = to_val(&self.env, a_xdr, "a")?;
            let b = to_val(&self.env, b_xdr, "b")?;
            let host: &soroban_env_host::Host = self.env.host();
            let ord = host.compare(&a, &b).map_err(|e| {
                // `HostError::error` IS `soroban_sdk::Error` (both crates share
                // one soroban-env-common), so no conversion is needed here.
                let c = errors::classify(e.error);
                failure("host", c.type_name, c.code, c.message)
            })?;
            Ok(match ord {
                std::cmp::Ordering::Less => -1,
                std::cmp::Ordering::Equal => 0,
                std::cmp::Ordering::Greater => 1,
            })
        })
    }

    fn max_ttl(&self) -> PyResult<u32> {
        contained(|| Ok(self.env.storage().max_ttl()))
    }

    #[pyo3(signature = (*, sequence_number=None, timestamp=None))]
    fn set_ledger(&self, sequence_number: Option<u32>, timestamp: Option<u64>) -> PyResult<()> {
        contained(|| {
            self.env.ledger().with_mut(|l| {
                if let Some(s) = sequence_number {
                    l.sequence_number = s;
                }
                if let Some(t) = timestamp {
                    l.timestamp = t;
                }
            });
            Ok(())
        })
    }

    fn register(&self, wasm: &[u8], constructor_args_xdr: Vec<Vec<u8>>) -> PyResult<String> {
        contained(|| {
            validate::check_wasm_header(wasm).map_err(invalid)?;
            let mut args: SorobanVec<Val> = SorobanVec::new(&self.env);
            for (i, b) in constructor_args_xdr.iter().enumerate() {
                args.push_back(to_val(&self.env, b, &format!("constructor arg {i}"))?);
            }
            // The sdk `Register` impl for `&[u8]` uploads + instantiates; a
            // host-side rejection of the module surfaces as a HostError the
            // sdk PANICS on (P3's `Env::register` row), which `contained`
            // turns into kind "panic" -- Task 1's Python test pins that a
            // garbage module is catchable.
            let addr = self.env.register(wasm, args);
            Ok(addr.to_string().to_string())
        })
    }

    fn invoke<'py>(
        &self,
        py: Python<'py>,
        contract: &str,
        function: &str,
        args_xdr: Vec<Vec<u8>>,
    ) -> PyResult<Bound<'py, PyBytes>> {
        contained(|| {
            let env = &self.env;
            let addr = address_of(env, contract)?;
            let sym = symbol_of(env, function)?;
            let mut args: SorobanVec<Val> = SorobanVec::new(env);
            for (i, b) in args_xdr.iter().enumerate() {
                args.push_back(to_val(env, b, &format!("arg {i}"))?);
            }
            self.invoked.set(true);
            match env.try_invoke_contract::<Val, soroban_sdk::Error>(&addr, &sym, args) {
                Ok(Ok(val)) => {
                    let scval = ScVal::try_from_val(env, &val)
                        .map_err(|e| conversion(format!("result: Val -> ScVal: {e:?}")))?;
                    xdr_bytes(py, &scval)
                }
                Ok(Err(e)) => Err(conversion(format!("result conversion: {e:?}"))),
                Err(Ok(e)) => {
                    let c = errors::classify(e);
                    Err(failure(
                        if c.is_contract { "contract" } else { "host" },
                        c.type_name,
                        c.code,
                        c.message,
                    ))
                }
                Err(Err(invoke_err)) => Err(failure("host", "", 0, format!("{invoke_err:?}"))),
            }
        })
    }

    fn mock_all_auths(&self) -> PyResult<()> {
        contained(|| {
            self.env.mock_all_auths();
            Ok(())
        })
    }

    fn mock_auths(&self, entries: Vec<(String, String, String, Vec<Vec<u8>>)>) -> PyResult<()> {
        contained(|| {
            let env = &self.env;
            // Build owned Addresses/Vecs first; MockAuth borrows them.
            let mut owned = Vec::new();
            for (who, contract, fn_name, args_xdr) in &entries {
                // CONTRACT strkeys only (review B2): the sdk registers a MockAuthContract at
                // `who`, which panics for an account address; account auth needs real
                // signatures and is M2. `address_of` pre-validates, so this is invalid_input.
                let who = address_of(env, who)?;
                let contract = address_of(env, contract)?;
                validate::check_symbol(fn_name).map_err(invalid)?;
                let mut args: std::vec::Vec<Val> = std::vec::Vec::new();
                for (i, b) in args_xdr.iter().enumerate() {
                    args.push(to_val(env, b, &format!("mock auth arg {i}"))?);
                }
                owned.push((who, contract, fn_name.clone(), args));
            }
            let invokes: Vec<MockAuthInvoke> = owned
                .iter()
                .map(|(_, c, f, a)| MockAuthInvoke {
                    contract: c,
                    fn_name: f.as_str(),
                    args: SorobanVec::from_slice(env, a),
                    sub_invokes: &[],
                })
                .collect();
            let mocks: Vec<MockAuth> = owned
                .iter()
                .zip(invokes.iter())
                .map(|((who, _, _, _), inv)| MockAuth {
                    address: who,
                    invoke: inv,
                })
                .collect();
            env.mock_auths(&mocks);
            Ok(())
        })
    }

    fn events<'py>(&self, py: Python<'py>) -> PyResult<Vec<Bound<'py, PyBytes>>> {
        contained(|| {
            self.env
                .events()
                .all()
                .events()
                .iter()
                .map(|ev| {
                    let b = ev
                        .to_xdr(Limits::none())
                        .map_err(|e| conversion(format!("event: {e:?}")))?;
                    Ok(PyBytes::new(py, &b))
                })
                .collect()
        })
    }

    fn auths<'py>(&self, py: Python<'py>) -> PyResult<Vec<AuthRow<'py>>> {
        contained(|| {
            let env = &self.env;
            env.auths()
                .into_iter()
                .map(|(who, inv)| {
                    // `register` itself records a CreateContractV2HostFn authorization
                    // (review M8): skip non-contract functions, never error on them.
                    let (contract, name, args) = match inv.function {
                        AuthorizedFunction::Contract((c, f, a)) => {
                            (c.to_string().to_string(), f.to_string(), a)
                        }
                        _ => return Ok(None),
                    };
                    let args = args
                        .iter()
                        .map(|v| {
                            let sc = ScVal::try_from_val(env, &v)
                                .map_err(|e| conversion(format!("auth arg: {e:?}")))?;
                            xdr_bytes(py, &sc)
                        })
                        .collect::<PyResult<Vec<_>>>()?;
                    Ok(Some((who.to_string().to_string(), contract, name, args)))
                })
                .filter_map(|r| r.transpose())
                .collect()
        })
    }

    fn storage_get<'py>(
        &self,
        py: Python<'py>,
        contract: &str,
        durability: &str,
        key_xdr: &[u8],
    ) -> PyResult<Option<Bound<'py, PyBytes>>> {
        contained(|| {
            let env = &self.env;
            // Everything the sdk could panic on is validated BEFORE the frame
            // push (P3): the durability first, then the address, then the key.
            let dur = durability_of(durability)?;
            let addr = address_of(env, contract)?;
            let key = to_val(env, key_xdr, "key")?;
            let got: Option<Val> = env.as_contract(&addr, || match dur {
                Durability::Persistent => env.storage().persistent().get::<Val, Val>(&key),
                Durability::Temporary => env.storage().temporary().get::<Val, Val>(&key),
                Durability::Instance => env.storage().instance().get::<Val, Val>(&key),
            });
            match got {
                None => Ok(None),
                Some(v) => {
                    let sc = ScVal::try_from_val(env, &v)
                        .map_err(|e| conversion(format!("stored value: {e:?}")))?;
                    Ok(Some(xdr_bytes(py, &sc)?))
                }
            }
        })
    }

    fn storage_has(&self, contract: &str, durability: &str, key_xdr: &[u8]) -> PyResult<bool> {
        contained(|| {
            let env = &self.env;
            let dur = durability_of(durability)?;
            let addr = address_of(env, contract)?;
            let key = to_val(env, key_xdr, "key")?;
            Ok(env.as_contract(&addr, || match dur {
                Durability::Persistent => env.storage().persistent().has::<Val>(&key),
                Durability::Temporary => env.storage().temporary().has::<Val>(&key),
                Durability::Instance => env.storage().instance().has::<Val>(&key),
            }))
        })
    }

    fn storage_set(
        &self,
        contract: &str,
        durability: &str,
        key_xdr: &[u8],
        value_xdr: &[u8],
    ) -> PyResult<()> {
        contained(|| {
            let env = &self.env;
            let dur = durability_of(durability)?;
            let addr = address_of(env, contract)?;
            let key = to_val(env, key_xdr, "key")?;
            let value = to_val(env, value_xdr, "value")?;
            env.as_contract(&addr, || match dur {
                Durability::Persistent => env.storage().persistent().set::<Val, Val>(&key, &value),
                Durability::Temporary => env.storage().temporary().set::<Val, Val>(&key, &value),
                Durability::Instance => env.storage().instance().set::<Val, Val>(&key, &value),
            });
            Ok(())
        })
    }

    /// RELATIVE ledgers remaining, EXCLUDING the current ledger (review B10):
    /// `live_until = sequence + ttl`. The testutils `get_ttl` PANICS on an
    /// absent or expired entry, so an absent entry is pre-checked with `has`
    /// and an expired one is caught by `unwind_to_none`; both are `None`. The
    /// instance form takes NO key, so a non-empty key there is a caller bug.
    fn storage_ttl(
        &self,
        contract: &str,
        durability: &str,
        key_xdr: &[u8],
    ) -> PyResult<Option<u32>> {
        contained(|| {
            let env = &self.env;
            let dur = durability_of(durability)?;
            let addr = address_of(env, contract)?;
            if dur == Durability::Instance {
                if !key_xdr.is_empty() {
                    return Err(invalid(
                        "durability \"instance\" addresses the whole instance entry and takes no \
                         key: pass b\"\""
                            .to_string(),
                    ));
                }
                return Ok(unwind_to_none(|| {
                    env.as_contract(&addr, || env.storage().instance().get_ttl())
                }));
            }
            let key = to_val(env, key_xdr, "key")?;
            let present = env.as_contract(&addr, || match dur {
                Durability::Persistent => env.storage().persistent().has::<Val>(&key),
                _ => env.storage().temporary().has::<Val>(&key),
            });
            if !present {
                return Ok(None);
            }
            Ok(unwind_to_none(|| {
                env.as_contract(&addr, || match dur {
                    Durability::Persistent => env.storage().persistent().get_ttl(&key),
                    _ => env.storage().temporary().get_ttl(&key),
                })
            }))
        })
    }

    fn budget(&self) -> PyResult<(u64, u64)> {
        contained(|| {
            let b = self.env.cost_estimate().budget();
            Ok((b.cpu_instruction_cost(), b.memory_bytes_cost()))
        })
    }

    fn resources<'py>(&self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyDict>>> {
        // `cost_estimate().resources()` PANICS before the first invocation (review
        // m14); the façade tracks whether an invoke has happened and this returns
        // None until then. Exhaustive destructure (review M10): a field the host
        // adds is a COMPILE error here, never a silent omission.
        if !self.invoked.get() {
            return Ok(None);
        }
        contained(|| {
            let soroban_env_host::InvocationResources {
                instructions,
                mem_bytes,
                disk_read_entries,
                memory_read_entries,
                write_entries,
                disk_read_bytes,
                write_bytes,
                contract_events_size_bytes,
                persistent_rent_ledger_bytes,
                persistent_entry_rent_bumps,
                temporary_rent_ledger_bytes,
                temporary_entry_rent_bumps,
            } = self.env.cost_estimate().resources();
            let d = PyDict::new(py);
            d.set_item("instructions", instructions)?;
            d.set_item("mem_bytes", mem_bytes)?;
            d.set_item("disk_read_entries", disk_read_entries)?;
            d.set_item("memory_read_entries", memory_read_entries)?;
            d.set_item("write_entries", write_entries)?;
            d.set_item("disk_read_bytes", disk_read_bytes)?;
            d.set_item("write_bytes", write_bytes)?;
            d.set_item("contract_events_size_bytes", contract_events_size_bytes)?;
            d.set_item("persistent_rent_ledger_bytes", persistent_rent_ledger_bytes)?;
            d.set_item("persistent_entry_rent_bumps", persistent_entry_rent_bumps)?;
            d.set_item("temporary_rent_ledger_bytes", temporary_rent_ledger_bytes)?;
            d.set_item("temporary_entry_rent_bumps", temporary_entry_rent_bumps)?;
            Ok(Some(d))
        })
    }
}

#[pymodule]
fn serpent_host(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RealEnv>()?;
    m.add("HostFailure", m.py().get_type::<HostFailure>())?;
    m.add("HOST_CRATE_VERSION", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}

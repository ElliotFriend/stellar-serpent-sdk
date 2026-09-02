//! macOS link flags for the extension-module cdylib (P8).
//!
//! Python symbols must stay unresolved until the interpreter loads the module.
//! `host/.cargo/config.toml` says the same thing, but cargo reads `.cargo/
//! config.toml` relative to the CURRENT WORKING DIRECTORY, not the manifest:
//! the documented build command runs from the repo root with `--manifest-path
//! host/Cargo.toml`, so that file is never read and the link fails with
//! "Undefined symbols: _PyBytes_FromStringAndSize, ...". `rustc-cdylib-link-arg`
//! travels with the crate instead, so `maturin develop` from the repo root and
//! `cargo build` from `host/` both link. (pyo3's own build script cannot do
//! this for us: `rustc-cdylib-link-arg` only applies to the package whose
//! build script emits it.)

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        println!("cargo:rustc-cdylib-link-arg=-undefined");
        println!("cargo:rustc-cdylib-link-arg=dynamic_lookup");
    }
}

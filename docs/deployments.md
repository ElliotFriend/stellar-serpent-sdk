# Deployments

Every contract this repository has deployed to Stellar testnet, with the
bytes it runs. Read-only simulations against these are the tier-3 fixtures
under `tests/real_host/fixtures/testnet/`.

| Contract | Network | Id | wasm sha256 | Built from | Notes |
|---|---|---|---|---|---|
| Phase 0 spike counter | testnet | `CDW6O3TM7MWE3PKT4PNHHA4QOYUV4TMP4G6G2KH4QW4H4RAY4OYSEOJI` | see `spikes/spike1/DEPLOY_LOG.md` | `spikes/spike1/contract_src.py` | frozen Phase 0 evidence; the spike1 goldens anchor to it |
| shapes (M1-E2 build) | testnet | `CDEU7Q4DYJVHL2NENDM263KNXOU73RHHWY2BUWBT2HZX6X4BF4FZ7GNW` | `6a9dd13549bac20f2609ab3d74668963b5249a7943dc7f027cdf6c42bec86e33` | `examples/shapes.py` before M1-F Task 0 | `area` traps (the B1 small-Symbol compare); superseded by the M1-end redeploy |

Task 11 appends the two M1-end rows.

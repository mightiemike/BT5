# Q873: CLVMStorage binding format auto magic-prefix boundary via mempool mode followed by block mode replay

## Question
Can an unprivileged attacker reach `CLVMStorage` in `wheel/python/clvm_rs/clvm_storage.py` through public Python/Rust binding API `CLVMStorage` with attacker-controlled Python or byte inputs, using a crafted format auto magic-prefix boundary input and the mempool mode followed by block mode replay validation path while controlling format auto/legacy/backrefs/2026 selection, so the code auto-detecting format more permissively than direct parser, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that LazyNode must expose exact allocator-backed result and causing High Python/Rust API divergence: callers see different result/cost/error/bytes/hash?

## Target
- File/function: wheel/python/clvm_rs/clvm_storage.py::CLVMStorage
- Entrypoint: public Python/Rust binding API `CLVMStorage` with attacker-controlled Python or byte inputs
- Attacker controls: format auto/legacy/backrefs/2026 selection
- Exploit idea: Build the smallest CLVM blob/program/API call for format auto magic-prefix boundary, drive it through mempool mode followed by block mode replay, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: LazyNode must expose exact allocator-backed result
- Expected Immunefi impact: High Python/Rust API divergence: callers see different result/cost/error/bytes/hash
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

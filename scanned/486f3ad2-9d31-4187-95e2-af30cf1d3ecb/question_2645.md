# Q2645: deserialize binding LazyNode pair then atom access via serde_2026 direct versus serde auto

## Question
Can an unprivileged attacker reach `deserialize` in `wheel/python/clvm_rs/serde.py` through public Python/Rust binding API `deserialize` with attacker-controlled Python or byte inputs, using a crafted LazyNode pair then atom access input and the serde_2026 direct versus serde auto validation path while controlling format auto/legacy/backrefs/2026 selection, so the code auto-detecting format more permissively than direct parser, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that LazyNode must expose exact allocator-backed result and causing High Python/Rust API divergence: callers see different result/cost/error/bytes/hash?

## Target
- File/function: wheel/python/clvm_rs/serde.py::deserialize
- Entrypoint: public Python/Rust binding API `deserialize` with attacker-controlled Python or byte inputs
- Attacker controls: format auto/legacy/backrefs/2026 selection
- Exploit idea: Build the smallest CLVM blob/program/API call for LazyNode pair then atom access, drive it through serde_2026 direct versus serde auto, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: LazyNode must expose exact allocator-backed result
- Expected Immunefi impact: High Python/Rust API divergence: callers see different result/cost/error/bytes/hash
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

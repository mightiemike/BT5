# Q2637: pair binding format auto magic-prefix boundary via default flags versus MEMPOOL_MODE

## Question
Can an unprivileged attacker reach `pair` in `wheel/python/clvm_rs/clvm_storage.py` through public Python/Rust binding API `pair` with attacker-controlled Python or byte inputs, using a crafted format auto magic-prefix boundary input and the default flags versus MEMPOOL_MODE validation path while controlling format auto/legacy/backrefs/2026 selection, so the code auto-detecting format more permissively than direct parser, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that LazyNode must expose exact allocator-backed result and causing Critical consensus divergence: binding changes accepted/rejected evaluation?

## Target
- File/function: wheel/python/clvm_rs/clvm_storage.py::pair
- Entrypoint: public Python/Rust binding API `pair` with attacker-controlled Python or byte inputs
- Attacker controls: format auto/legacy/backrefs/2026 selection
- Exploit idea: Build the smallest CLVM blob/program/API call for format auto magic-prefix boundary, drive it through default flags versus MEMPOOL_MODE, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: LazyNode must expose exact allocator-backed result
- Expected Immunefi impact: Critical consensus divergence: binding changes accepted/rejected evaluation
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q245: curried values tree hash binding LazyNode pair then atom access via execute then serialize backrefs

## Question
Can an unprivileged attacker reach `curried_values_tree_hash` in `wheel/python/clvm_rs/curry_and_treehash.py` through public Python/Rust binding API `curried_values_tree_hash` with attacker-controlled Python or byte inputs, using a crafted LazyNode pair then atom access input and the execute then serialize backrefs validation path while controlling format auto/legacy/backrefs/2026 selection, so the code auto-detecting format more permissively than direct parser, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that Python conversion must snapshot one stable tree and causing Critical consensus divergence: binding changes accepted/rejected evaluation?

## Target
- File/function: wheel/python/clvm_rs/curry_and_treehash.py::curried_values_tree_hash
- Entrypoint: public Python/Rust binding API `curried_values_tree_hash` with attacker-controlled Python or byte inputs
- Attacker controls: format auto/legacy/backrefs/2026 selection
- Exploit idea: Build the smallest CLVM blob/program/API call for LazyNode pair then atom access, drive it through execute then serialize backrefs, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: Python conversion must snapshot one stable tree
- Expected Immunefi impact: Critical consensus divergence: binding changes accepted/rejected evaluation
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

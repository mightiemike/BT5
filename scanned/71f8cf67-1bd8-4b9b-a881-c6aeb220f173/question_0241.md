# Q241: to atom type binding mutable Python object during conversion via pair path all-left versus all-right

## Question
Can an unprivileged attacker reach `to_atom_type` in `wheel/python/clvm_rs/casts.py` through public Python/Rust binding API `to_atom_type` with attacker-controlled Python or byte inputs, using a crafted mutable Python object during conversion input and the pair path all-left versus all-right validation path while controlling format auto/legacy/backrefs/2026 selection, so the code auto-detecting format more permissively than direct parser, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that Python conversion must snapshot one stable tree and causing High Python/Rust API divergence: callers see different result/cost/error/bytes/hash?

## Target
- File/function: wheel/python/clvm_rs/casts.py::to_atom_type
- Entrypoint: public Python/Rust binding API `to_atom_type` with attacker-controlled Python or byte inputs
- Attacker controls: format auto/legacy/backrefs/2026 selection
- Exploit idea: Build the smallest CLVM blob/program/API call for mutable Python object during conversion, drive it through pair path all-left versus all-right, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: Python conversion must snapshot one stable tree
- Expected Immunefi impact: High Python/Rust API divergence: callers see different result/cost/error/bytes/hash
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

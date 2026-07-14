# Q745: to clvm object binding mutable Python object during conversion via cost limit at exact operator boundary

## Question
Can an unprivileged attacker reach `to_clvm_object` in `wheel/python/clvm_rs/casts.py` through public Python/Rust binding API `to_clvm_object` with attacker-controlled Python or byte inputs, using a crafted mutable Python object during conversion input and the cost limit at exact operator boundary validation path while controlling format auto/legacy/backrefs/2026 selection, so the code auto-detecting format more permissively than direct parser, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that Python conversion must snapshot one stable tree and causing Critical tree identity corruption: Python conversion exposes wrong tree?

## Target
- File/function: wheel/python/clvm_rs/casts.py::to_clvm_object
- Entrypoint: public Python/Rust binding API `to_clvm_object` with attacker-controlled Python or byte inputs
- Attacker controls: format auto/legacy/backrefs/2026 selection
- Exploit idea: Build the smallest CLVM blob/program/API call for mutable Python object during conversion, drive it through cost limit at exact operator boundary, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: Python conversion must snapshot one stable tree
- Expected Immunefi impact: Critical tree identity corruption: Python conversion exposes wrong tree
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

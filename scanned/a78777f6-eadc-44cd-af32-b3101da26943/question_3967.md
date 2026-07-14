# Q3967: op read sexp binding mutable Python object during conversion via tree_hash before and after intern_tree

## Question
Can an unprivileged attacker reach `_op_read_sexp` in `wheel/python/clvm_rs/ser.py` through public Python/Rust binding API `_op_read_sexp` with attacker-controlled Python or byte inputs, using a crafted mutable Python object during conversion input and the tree_hash before and after intern_tree validation path while controlling format auto/legacy/backrefs/2026 selection, so the code auto-detecting format more permissively than direct parser, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that auto detection must not accept bytes direct parser rejects and causing Critical tree identity corruption: Python conversion exposes wrong tree?

## Target
- File/function: wheel/python/clvm_rs/ser.py::_op_read_sexp
- Entrypoint: public Python/Rust binding API `_op_read_sexp` with attacker-controlled Python or byte inputs
- Attacker controls: format auto/legacy/backrefs/2026 selection
- Exploit idea: Build the smallest CLVM blob/program/API call for mutable Python object during conversion, drive it through tree_hash before and after intern_tree, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: auto detection must not accept bytes direct parser rejects
- Expected Immunefi impact: Critical tree identity corruption: Python conversion exposes wrong tree
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

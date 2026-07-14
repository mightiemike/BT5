# Q1129: str binding mutable Python object during conversion via object cache cold versus warm execution

## Question
Can an unprivileged attacker reach `__str__` in `wheel/python/clvm_rs/eval_error.py` through public Python/Rust binding API `__str__` with attacker-controlled Python or byte inputs, using a crafted mutable Python object during conversion input and the object cache cold versus warm execution validation path while controlling format auto/legacy/backrefs/2026 selection, so the code auto-detecting format more permissively than direct parser, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that LazyNode must expose exact allocator-backed result and causing Critical tree identity corruption: Python conversion exposes wrong tree?

## Target
- File/function: wheel/python/clvm_rs/eval_error.py::__str__
- Entrypoint: public Python/Rust binding API `__str__` with attacker-controlled Python or byte inputs
- Attacker controls: format auto/legacy/backrefs/2026 selection
- Exploit idea: Build the smallest CLVM blob/program/API call for mutable Python object during conversion, drive it through object cache cold versus warm execution, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: LazyNode must expose exact allocator-backed result
- Expected Immunefi impact: Critical tree identity corruption: Python conversion exposes wrong tree
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

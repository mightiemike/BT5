# Q3087: handle obj binding format auto magic-prefix boundary via object cache cold versus warm execution

## Question
Can an unprivileged attacker reach `handle_obj` in `wheel/python/clvm_rs/tree_hash.py` through public Python/Rust binding API `handle_obj` with attacker-controlled Python or byte inputs, using a crafted format auto magic-prefix boundary input and the object cache cold versus warm execution validation path while controlling format auto/legacy/backrefs/2026 selection, so the code auto-detecting format more permissively than direct parser, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that Python and Rust APIs must agree on result/cost/error/bytes/hash and causing Critical tree identity corruption: Python conversion exposes wrong tree?

## Target
- File/function: wheel/python/clvm_rs/tree_hash.py::handle_obj
- Entrypoint: public Python/Rust binding API `handle_obj` with attacker-controlled Python or byte inputs
- Attacker controls: format auto/legacy/backrefs/2026 selection
- Exploit idea: Build the smallest CLVM blob/program/API call for format auto magic-prefix boundary, drive it through object cache cold versus warm execution, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: Python and Rust APIs must agree on result/cost/error/bytes/hash
- Expected Immunefi impact: Critical tree identity corruption: Python conversion exposes wrong tree
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

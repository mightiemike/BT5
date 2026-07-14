# Q563: from bytes 2026 binding LazyNode pair then atom access via stream hash versus tree hash

## Question
Can an unprivileged attacker reach `from_bytes_2026` in `wheel/python/clvm_rs/program.py` through public Python/Rust binding API `from_bytes_2026` with attacker-controlled Python or byte inputs, using a crafted LazyNode pair then atom access input and the stream hash versus tree hash validation path while controlling format auto/legacy/backrefs/2026 selection, so the code auto-detecting format more permissively than direct parser, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that Python and Rust APIs must agree on result/cost/error/bytes/hash and causing High Python/Rust API divergence: callers see different result/cost/error/bytes/hash?

## Target
- File/function: wheel/python/clvm_rs/program.py::from_bytes_2026
- Entrypoint: public Python/Rust binding API `from_bytes_2026` with attacker-controlled Python or byte inputs
- Attacker controls: format auto/legacy/backrefs/2026 selection
- Exploit idea: Build the smallest CLVM blob/program/API call for LazyNode pair then atom access, drive it through stream hash versus tree hash, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: Python and Rust APIs must agree on result/cost/error/bytes/hash
- Expected Immunefi impact: High Python/Rust API divergence: callers see different result/cost/error/bytes/hash
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.
